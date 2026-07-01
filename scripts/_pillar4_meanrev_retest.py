"""Pillar 4 / brief sec.6 — meanrev z x lookback grid RE-TEST (net of cost).

Pre-registered (docs/PILLAR4_EVENT_DRIVEN.md sec.5): z_entry in {1.0,1.5,2.0,2.5}
x lookback in {250,500,750} = 12 trials. Select on IS (2017-2023) walk-forward,
evaluate the single winner ONCE on the untouched hold-out (2024-present), deflate
by n_trials=12, PBO via CSCV over the 12 IS curves, MC P(SR<0) on the winner.

Faithful to strategies/meanrev: same qualification (hedge-ratio bounds, ADF
p<=0.05, OU half-life in [10,200], split-half kappa-stability <=2.5), same
z-entry/exit/stop/time-stop logic, max 5 concurrent pairs. NET of the real
Indian cost stack (quantsys.costs.CostModel): long leg = delivery equity, short
leg = SSF future + 1.5%/yr financing (brief sec.5). No-look-ahead: pairs are
scanned on a trailing window and traded only on later days.

DISCLOSED CAVEAT: this is a DAILY-timeframe re-test (the local survivorship-free
panel); the live engine runs meanrev at ~45-min bars. Daily is lower-noise and
generous to cointegration, so a daily null is strong evidence.
"""
from __future__ import annotations

import math
from datetime import date
from itertools import combinations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

from quantsys.backtest.metrics import deflated_sharpe, monte_carlo_resample
from quantsys.config import load_config
from quantsys.core.types import Instrument, InstrumentKind
from quantsys.costs import CostModel
from quantsys.research.bhavcopy import build_panel, close_panel
from quantsys.research.validation import pbo_cscv

ADF_ALPHA, MIN_HL, MAX_HL, KAPPA_STAB = 0.05, 10.0, 200.0, 2.5
Z_EXIT, Z_STOP, TIME_STOP_HL, MAX_PAIRS, RESCAN_D = 0.5, 3.5, 3.0, 5, 21
Z_GRID = [1.0, 1.5, 2.0, 2.5]
LB_GRID = [250, 500, 750]
TRADING_DAYS = 252
IS_END = pd.Timestamp("2023-12-31")


def _ou_kappa(resid):
    if len(resid) < 30:
        return None, 0.0
    r0, r1 = resid[:-1], resid[1:]
    d = float(np.dot(r0, r0))
    if d <= 0:
        return None, 0.0
    phi = float(np.dot(r0, r1) / d)
    if not (0.0 < phi < 1.0):
        return None, 0.0
    k = -math.log(phi)
    return k, math.log(2.0) / k


def qualify(y_px, x_px, lookback):
    if len(y_px) < lookback or len(x_px) < lookback:
        return None
    y, x = np.log(y_px[-lookback:]), np.log(x_px[-lookback:])
    if not (np.all(np.isfinite(y)) and np.all(np.isfinite(x))):
        return None
    vx = float(np.var(x))
    if vx < 1e-12:
        return None
    beta = float(np.cov(x, y, bias=True)[0, 1] / vx)
    if not (0.1 <= beta <= 10.0):
        return None
    alpha = float(y.mean() - beta * x.mean())
    resid = y - (alpha + beta * x)
    try:
        p = float(adfuller(resid, regression="c", autolag="AIC")[1])
    except Exception:
        return None
    if p > ADF_ALPHA:
        return None
    kf, hl = _ou_kappa(resid)
    if kf is None or not (MIN_HL <= hl <= MAX_HL):
        return None
    half = len(resid) // 2
    k1, _ = _ou_kappa(resid[:half])
    k2, _ = _ou_kappa(resid[half:])
    if k1 is None or k2 is None or max(k1, k2) / max(min(k1, k2), 1e-12) > KAPPA_STAB:
        return None
    return {"beta": beta, "alpha": alpha, "theta": float(resid.mean()) + alpha,
            "sigma": float(resid.std()) or 1e-9, "hl": hl, "p": p}


def _cost_fractions(cfg):
    """Round-trip cost FRACTION of notional for each leg (proportional part)."""
    cm = CostModel(cfg.costs)
    eq = Instrument("EQ", kind=InstrumentKind.EQUITY, lot_size=1, point_value=1.0)
    fu = Instrument("FU", kind=InstrumentKind.FUTURE, lot_size=1, point_value=1.0)
    big, px = 1_000_000.0, 1000.0
    long_rt = cm.round_trip(eq, big, px, delivery=True) / (big * px)     # long = delivery equity
    short_rt = cm.round_trip(fu, big, px, delivery=False) / (big * px)   # short = SSF future
    return long_rt, short_rt


def simulate(wide_log, pairs_by_scan, scan_dates, z_entry, lookback, costs, fin_daily,
             trades=None):
    """Daily portfolio returns for one (z_entry, lookback) config.

    No look-ahead: at scan date s we use pairs qualified on data <= s and trade
    them only on days > s, until the next scan. Position beta/theta/sigma are
    FROZEN at scan time. Returns a pd.Series indexed by trade dates.
    """
    long_rt, short_rt = costs
    entry_cost = exit_cost = (long_rt + short_rt) / 2.0
    idx = wide_log.index
    n = len(idx)
    daily = np.zeros(n)
    w = 1.0 / MAX_PAIRS
    for si, s in enumerate(scan_dates):
        pairs = pairs_by_scan.get((lookback, s), [])[:MAX_PAIRS]
        s_i = idx.get_indexer([s])[0]
        end_i = (idx.get_indexer([scan_dates[si + 1]])[0]
                 if si + 1 < len(scan_dates) else n)
        if s_i < 0:
            continue
        for pr in pairs:
            a, b, beta, sigma, hl = pr["a"], pr["b"], pr["beta"], pr["sigma"], pr["hl"]
            ya, xb = wide_log[a].to_numpy(), wide_log[b].to_numpy()
            # resid is the centered spread (spread - alpha); the in-sample mean is
            # 0 by OLS, so z = resid/sigma directly (do NOT subtract alpha again).
            resid = ya - (pr["alpha"] + beta * xb)
            pos, ent_i, ent_res, ent_z, tstop = 0, -1, 0.0, 0.0, int(round(TIME_STOP_HL * hl))
            for i in range(s_i + 1, min(end_i, n)):
                if not (np.isfinite(resid[i]) and np.isfinite(resid[i - 1])):
                    continue
                z = resid[i] / sigma
                if pos != 0:                                   # carry P&L
                    daily[i] += w * pos * (resid[i] - resid[i - 1])
                    daily[i] -= w * 0.5 * fin_daily            # financing on short ~half notional
                    reason = ("converge" if abs(z) <= Z_EXIT else "stop" if abs(z) >= Z_STOP
                              else "time" if (i - ent_i) >= tstop else None)
                    if reason:
                        daily[i] -= w * exit_cost
                        if trades is not None:
                            g = pos * (resid[i] - ent_res)
                            trades.append({"ent_z": ent_z, "reason": reason, "hold": i - ent_i,
                                           "gross": g, "net": g - entry_cost - exit_cost})
                        pos = 0
                elif abs(z) >= z_entry:                        # enter against the move
                    pos, ent_i, ent_res, ent_z = (-1 if z > 0 else 1), i, resid[i], z
                    daily[i] -= w * entry_cost
            if pos != 0:                                       # close at window end
                daily[min(end_i, n) - 1] -= w * exit_cost
                if trades is not None:
                    g = pos * (resid[min(end_i, n) - 1] - ent_res)
                    trades.append({"ent_z": ent_z, "reason": "rescan", "hold": min(end_i, n) - 1 - ent_i,
                                   "gross": g, "net": g - entry_cost - exit_cost})
    return pd.Series(daily, index=idx)


def _metrics(r):
    r = r[np.isfinite(r)]
    if r.std(ddof=1) == 0 or len(r) < 20:
        return {"sharpe": 0.0, "cagr": 0.0, "maxdd": 0.0, "n": len(r)}
    sharpe = float(r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS))
    eq = (1 + r).cumprod()
    cagr = float(eq.iloc[-1] ** (TRADING_DAYS / len(r)) - 1)
    maxdd = float((eq / eq.cummax() - 1).min())
    return {"sharpe": sharpe, "cagr": cagr, "maxdd": maxdd, "n": int((r != 0).sum())}


def _self_test():
    """Verify the P&L SIMULATOR (the qualifier is already validated by the
    diagnosis on real data): a constructed mean-reverting spread entered at
    extreme z must yield POSITIVE gross P&L; a non-reverting random walk must
    not. We build a pre-qualified pair directly so the test isolates the
    simulator from the qualifier's (deliberately strict) filters."""
    rng = np.random.default_rng(0)
    n = 800
    resid = np.zeros(n)                         # OU spread, beta=1 by construction
    for i in range(1, n):
        resid[i] = 0.95 * resid[i - 1] + rng.normal(0, 0.01)
    base = np.linspace(0.0, 3.0, n)
    idx = pd.bdate_range("2015-01-01", periods=n)
    wl = pd.DataFrame({"A": base + resid, "B": base}, index=idx)  # log prices
    pr = {"a": "A", "b": "B", "beta": 1.0, "alpha": 0.0,
          "theta": float(resid.mean()), "sigma": float(resid.std()), "hl": 13.0}
    pbs = {(300, idx[350]): [pr]}
    gross = simulate(wl, pbs, [idx[350], idx[-1]], 1.0, 300, (0.0, 0.0), 0.0).sum()
    assert gross > 0, f"reverting spread should profit gross, got {gross:.4f}"

    rw = np.cumsum(rng.normal(0, 0.01, n))      # non-cointegrated: A,B independent walks
    wl2 = pd.DataFrame({"A": rw, "B": np.cumsum(rng.normal(0, 0.01, n))}, index=idx)
    pr2 = {**pr, "theta": 0.0, "sigma": float(np.std(rw))}
    net_rw = simulate(wl2, {(300, idx[350]): [pr2]}, [idx[350], idx[-1]],
                      1.0, 300, (0.002, 0.001), 0.0).sum()
    assert net_rw < gross, "random walk must not beat a true reverting spread"
    print(f"  self-test OK (gross reverting={gross:.3f} > 0; random-walk net={net_rw:.3f})")


def main():
    _self_test()
    cfg = load_config("config/base.yaml")
    costs = _cost_fractions(cfg)
    fin_daily = 0.015 / 365.0
    print(f"cost fractions: long(delivery eq) RT={costs[0]*1e4:.0f}bps  "
          f"short(SSF fut) RT={costs[1]*1e4:.0f}bps  financing={0.015*100:.1f}%/yr")

    sector = {u.symbol: u.sector for u in cfg.universe
              if u.kind == InstrumentKind.EQUITY and u.sector}
    syms = sorted(sector)
    pairs = [(a, b) for a, b in combinations(syms, 2) if sector[a] == sector[b]]

    print("loading daily panel 2016-01..2026-06 (cache)...")
    tidy = build_panel(date(2016, 1, 1), date(2026, 6, 19), progress_every=0)
    wide = close_panel(tidy).sort_index()
    have = [s for s in syms if s in wide.columns]
    wide = wide[have].dropna(how="all")
    wide_log = np.log(wide)
    pairs = [(a, b) for a, b in pairs if a in have and b in have]
    print(f"panel {wide.shape[0]} days {wide.index.min().date()}..{wide.index.max().date()}; "
          f"{len(pairs)} same-sector pairs")

    # monthly scan dates that have >=max(lookback) history before them
    scan_dates = list(wide.index[max(LB_GRID)::RESCAN_D])
    print(f"scan dates: {len(scan_dates)}  precomputing qualifying pairs per lookback...")
    pairs_by_scan = {}
    for lb in LB_GRID:
        for s in scan_dates:
            s_i = wide.index.get_indexer([s])[0]
            if s_i < lb:
                continue
            win = wide.iloc[s_i - lb:s_i]
            qs = []
            for a, b in pairs:
                ya, xb = win[a].to_numpy(float), win[b].to_numpy(float)
                if np.isnan(ya).any() or np.isnan(xb).any():
                    continue
                q = qualify(ya, xb, lb)
                if q:
                    qs.append({"a": a, "b": b, **q})
            qs.sort(key=lambda d: d["p"])           # strongest cointegration first
            pairs_by_scan[(lb, s)] = qs
    print("  done. running 12-config grid...")

    for zc in (1.0, 2.5):                       # probe: does z_entry change anything?
        tr = []
        simulate(wide_log, pairs_by_scan, scan_dates, zc, 250, costs, fin_daily, trades=tr)
        if tr:
            d = pd.DataFrame(tr)
            print(f"  [probe z={zc} lb=250] n_trades={len(d)} mean|entry_z|={d['ent_z'].abs().mean():.2f} "
                  f"win%={100*(d['net']>0).mean():.0f} gross/trade={d['gross'].mean()*1e4:.0f}bps "
                  f"net/trade={d['net'].mean()*1e4:.0f}bps exits={d['reason'].value_counts().to_dict()}")

    # ---- run grid; split IS / hold-out ----
    is_curves, results = {}, []
    for lb in LB_GRID:
        for z in Z_GRID:
            r = simulate(wide_log, pairs_by_scan, scan_dates, z, lb, costs, fin_daily)
            is_r = r[r.index <= IS_END]
            oos_r = r[r.index > IS_END]
            is_curves[(z, lb)] = is_r
            results.append({"z": z, "lb": lb, "is": _metrics(is_r), "oos": _metrics(oos_r), "r": r})

    print("\n config (z,lookback)   IS_Sharpe  IS_CAGR   OOS_Sharpe OOS_CAGR  OOS_maxDD  trades")
    for x in results:
        print(f"  z={x['z']:<4} lb={x['lb']:<4}   {x['is']['sharpe']:8.2f}  "
              f"{x['is']['cagr']*100:6.1f}%   {x['oos']['sharpe']:8.2f}  "
              f"{x['oos']['cagr']*100:6.1f}%  {x['oos']['maxdd']*100:7.1f}%  {x['oos']['n']}")

    # ---- pre-registered selection: best IS Sharpe -> evaluate ONCE on hold-out ----
    winner = max(results, key=lambda x: x["is"]["sharpe"])
    z, lb = winner["z"], winner["lb"]
    oos = winner["oos"]
    oos_r = winner["r"][winner["r"].index > IS_END]
    dsr = deflated_sharpe(oos["sharpe"], len(oos_r[oos_r != 0]) or len(oos_r),
                          n_trials=len(Z_GRID) * len(LB_GRID))
    # PBO via CSCV over the 12 IS daily curves (aligned)
    mat = pd.DataFrame({k: v for k, v in is_curves.items()}).fillna(0.0).to_numpy()
    pbo = pbo_cscv(mat, n_splits=10)
    eq = (1 + oos_r).cumprod()
    mc = monte_carlo_resample(list(zip(oos_r.index, eq.to_numpy())))

    print("\n==== PRE-REGISTERED WINNER (selected on IS) ====")
    print(f"config: z_entry={z}, lookback={lb}")
    print(f"HOLD-OUT (2024+): Sharpe={oos['sharpe']:.2f}  CAGR={oos['cagr']*100:.1f}%  "
          f"maxDD={oos['maxdd']*100:.1f}%  trades={oos['n']}")
    print(f"Deflated Sharpe (n_trials=12) = {dsr}")
    print(f"PBO (CSCV) = {pbo.get('pbo')}")
    print(f"MC P(SR<0) = {mc.get('p_sharpe_negative')}")
    gate = {
        "OOS Sharpe>=0.80": oos["sharpe"] >= 0.80,
        "Deflated>=0.95": (dsr or 0) >= 0.95,
        "P(SR<0)<=0.10": (mc.get("p_sharpe_negative", 1.0)) <= 0.10,
        "PBO<=0.50": (pbo.get("pbo", 1.0)) <= 0.50,
        "net CAGR>0": oos["cagr"] > 0,
    }
    print("\n GATE (all 5 must pass):")
    for k, v in gate.items():
        print(f"   [{'PASS' if v else 'FAIL'}] {k}")
    print("\nVERDICT:", "CANDIDATE — clears the gate" if all(gate.values())
          else "DEAD — does not clear the gate (report honest numbers, STOP per stop-rule)")


if __name__ == "__main__":
    main()
