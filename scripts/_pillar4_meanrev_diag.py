"""Pillar 4 / brief sec.6 — meanrev cointegration DIAGNOSIS (daily timeframe).

Question to answer BEFORE any z-threshold grid: is meanrev's dormancy a wrong
threshold or an ABSENCE of qualifying cointegrated pairs? A z-threshold cannot
fix a no-signal problem. We faithfully replicate strategies/meanrev._fit_pair
(hedge-ratio bounds -> ADF p<=0.05 -> OU half-life in [10,200] -> split-half
kappa-stability <=2.5) on the local survivorship-free daily bhavcopy panel over
the IS window (2017-2023), for same-sector pairs in the live config universe.

NOTE: the live engine runs meanrev on ~45-min bars; this is a DAILY-timeframe
diagnosis (the local data), where half-life units are days. Disclosed as such.
"""
from __future__ import annotations

import math
from datetime import date
from itertools import combinations

import numpy as np
from statsmodels.tsa.stattools import adfuller

from quantsys.config import load_config
from quantsys.core.types import InstrumentKind
from quantsys.research.bhavcopy import build_panel, close_panel

ADF_ALPHA, MIN_HL, MAX_HL, KAPPA_STAB = 0.05, 10.0, 200.0, 2.5
LOOKBACK = 500          # default meanrev lookback (strategy bars == days here)


def _ou_kappa(resid: np.ndarray):
    if len(resid) < 30:
        return None, 0.0
    r0, r1 = resid[:-1], resid[1:]
    denom = float(np.dot(r0, r0))
    if denom <= 0:
        return None, 0.0
    phi = float(np.dot(r0, r1) / denom)
    if not (0.0 < phi < 1.0):
        return None, 0.0
    k = -math.log(phi)
    return k, math.log(2.0) / k


def qualifies(y_px: np.ndarray, x_px: np.ndarray) -> dict | None:
    """Faithful port of meanrev._fit_pair on two price series."""
    if len(y_px) < LOOKBACK or len(x_px) < LOOKBACK:
        return None
    y, x = np.log(y_px[-LOOKBACK:]), np.log(x_px[-LOOKBACK:])
    if not (np.all(np.isfinite(y)) and np.all(np.isfinite(x))):
        return None
    vx = float(np.var(x))
    if vx < 1e-12:
        return None
    beta = float(np.cov(x, y, bias=True)[0, 1] / vx)
    if not (0.1 <= beta <= 10.0):
        return None
    resid = y - (float(y.mean() - beta * x.mean()) + beta * x)
    try:
        if float(adfuller(resid, regression="c", autolag="AIC")[1]) > ADF_ALPHA:
            return None
    except Exception:
        return None
    kf, hl = _ou_kappa(resid)
    if kf is None or not (MIN_HL <= hl <= MAX_HL):
        return None
    half = len(resid) // 2
    k1, _ = _ou_kappa(resid[:half])
    k2, _ = _ou_kappa(resid[half:])
    if k1 is None or k2 is None or max(k1, k2) / max(min(k1, k2), 1e-12) > KAPPA_STAB:
        return None
    return {"beta": beta, "half_life": hl}


def main() -> None:
    cfg = load_config("config/base.yaml")
    sector = {u.symbol: u.sector for u in cfg.universe
              if u.kind == InstrumentKind.EQUITY and u.sector}
    syms = sorted(sector)
    pairs = [(a, b) for a, b in combinations(syms, 2) if sector[a] == sector[b]]
    print(f"universe={len(syms)} equities, same-sector candidate pairs={len(pairs)}")

    print("loading daily bhavcopy panel 2017-2023 (from cache)...")
    tidy = build_panel(date(2017, 1, 1), date(2023, 12, 31), progress_every=0)
    wide = close_panel(tidy).sort_index()
    have = [s for s in syms if s in wide.columns]
    miss = [s for s in syms if s not in wide.columns]
    print(f"symbols present in panel: {len(have)}/{len(syms)}  missing={miss}")
    print(f"panel: {wide.shape[0]} trading days {wide.index.min().date()}..{wide.index.max().date()}")

    # rolling 500-day windows stepped quarterly; count qualifying pairs per as-of
    counts, ever_qualified = [], set()
    idx = list(range(LOOKBACK, wide.shape[0], 63))
    for end_i in idx:
        win = wide.iloc[end_i - LOOKBACK:end_i]
        q = 0
        for a, b in pairs:
            ya, xb = win[a].to_numpy(float), win[b].to_numpy(float)
            if np.isnan(ya).any() or np.isnan(xb).any():
                continue
            if qualifies(ya, xb):
                q += 1
                ever_qualified.add((a, b))
        counts.append(q)
        print(f"  as-of {win.index[-1].date()}: {q} qualifying same-sector pairs")

    counts = np.array(counts)
    print("\n==== DIAGNOSIS (daily, 2017-2023 IS) ====")
    print(f"as-of windows tested: {len(counts)}")
    print(f"qualifying pairs per window: mean={counts.mean():.1f} max={counts.max()} "
          f"min={counts.min()}")
    print(f"windows with >=1 qualifying pair: {int((counts >= 1).sum())}/{len(counts)} "
          f"({100 * (counts >= 1).mean():.0f}%)")
    print(f"distinct pairs that EVER qualified: {len(ever_qualified)} of {len(pairs)}")
    verdict = ("PAIRS FORM -> dormancy is NOT a no-signal problem; binding cause is "
               "downstream (entry z / cost gate / after-cost edge)"
               if counts.max() >= 1 else
               "NO qualifying pairs -> a no-signal problem; NO z-threshold can fix it")
    print("VERDICT:", verdict)


if __name__ == "__main__":
    main()
