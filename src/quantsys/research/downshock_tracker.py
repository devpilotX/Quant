"""Forward paper-tracker for the Pillar-4 DOWN-SHOCK underreaction signal.

The gated backtest (docs/PILLAR4_EVENT_DRIVEN.md §8a) found a market-best lead that
nonetheless FAILED the deflated-Sharpe gate (0.46 < 0.95) — most likely favourable
regime / small-sample luck (OOS Sharpe 1.75 > IS 0.57). The ONLY honest way to tell
real edge from luck is to watch it FORWARD, out-of-sample in real time, never by
re-fitting the 2016-2026 sample.

This module does exactly that. The config is FROZEN from the backtest and NEVER tuned:
a 4σ + 2×-volume DOWN day → market-NEUTRAL SHORT (beta-hedged) the next session, hold
10 trading days. Each run recomputes the forward record over [tracking_start, asof]
(idempotent), where ``tracking_start`` is frozen at the first run, so every recorded
event is genuinely after the backtest sample. It trades NOTHING — it only logs P&L the
signal WOULD have made, at zero risk. If the edge survives forward, it earns a real
deployment case; if it was luck, you watch it fade.

CLI:  python -m quantsys.research.downshock_tracker --state <json> --cache <dir>
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

# FROZEN, pre-registered (the gated-backtest winner). DO NOT TUNE.
Z_THRESH, V_THRESH, HOLD, DECLUSTER = 3.5, 2.0, 10, 30
EST = (-250, -30)
MIN_EST, MAX_CONC = 120, 5
COST_RT = 0.0017                       # 2-leg SSF round trip (~17 bps)
FIN_DAILY = 0.015 / 365.0
TRADING_DAYS = 252
TRAIL_DAYS = 360                       # history to (re)build before tracking_start


def _ols(ri: np.ndarray, rm: np.ndarray) -> tuple[float, float]:
    x = np.column_stack([np.ones_like(rm), rm])
    coef, *_ = np.linalg.lstsq(x, ri, rcond=None)
    return float(coef[0]), float(coef[1])


def down_events(rets, vol, syms, idx):
    sigma = rets.rolling(60).std().shift(1)
    z = rets / sigma
    vratio = vol / vol.rolling(20).mean().shift(1)
    pos = {d: i for i, d in enumerate(idx)}
    out = []
    for s in syms:
        mask = (z[s] <= -Z_THRESH) & (vratio[s] >= V_THRESH)
        last = -10**9
        for d in idx[mask.to_numpy(na_value=False)]:
            i = pos[d]
            if i - last >= DECLUSTER:
                last = i
                out.append((s, i))
    return out


def sleeve_daily(rets, market, events, idx):
    """Market-neutral short daily book return (−AR over [+1,+HOLD], ≤MAX_CONC)."""
    n = len(idx)
    daily = np.zeros(n)
    w = 1.0 / MAX_CONC
    rm = market.to_numpy()
    active_until: list[int] = []
    for s, e0 in sorted(events, key=lambda t: t[1]):
        est_lo, est_hi, ev_lo, ev_hi = e0 + EST[0], e0 + EST[1], e0 + 1, e0 + HOLD
        if est_lo < 0 or ev_hi >= n:
            continue
        active_until = [x for x in active_until if x > e0]
        if len(active_until) >= MAX_CONC:
            continue
        ri = rets[s].to_numpy()
        fin = np.isfinite(ri[est_lo:est_hi + 1]) & np.isfinite(rm[est_lo:est_hi + 1])
        if int(fin.sum()) < MIN_EST:
            continue
        a, b = _ols(ri[est_lo:est_hi + 1][fin], rm[est_lo:est_hi + 1][fin])
        ar = ri[ev_lo:ev_hi + 1] - (a + b * rm[ev_lo:ev_hi + 1])
        if not np.all(np.isfinite(ar)):
            continue
        daily[ev_lo:ev_hi + 1] += w * (-ar)
        daily[ev_lo] -= w * COST_RT / 2.0
        daily[ev_hi] -= w * COST_RT / 2.0
        daily[ev_lo:ev_hi + 1] -= w * FIN_DAILY
        active_until.append(ev_hi)
    return pd.Series(daily, index=idx)


def forward_record(rets, vol, idx, start):
    """Daily book return series for events whose ENTRY (+1) is on/after ``start``."""
    syms = list(rets.columns)
    market = rets.mean(axis=1)
    evs = [(s, i) for s, i in down_events(rets, vol, syms, idx)
           if i + 1 < len(idx) and idx[i + 1] >= start]
    daily = sleeve_daily(rets, market, evs, idx)
    fwd = daily[idx >= start]
    return fwd, evs


def summarize(fwd: pd.Series) -> dict:
    act = fwd[fwd != 0]
    out = {"forward_days": int(len(act)),
           "forward_cum_return": float((1 + fwd).prod() - 1)}
    if len(act) >= 20 and fwd.std(ddof=1) > 0:
        out["forward_sharpe_ann"] = float(fwd.mean() / fwd.std(ddof=1)
                                          * math.sqrt(TRADING_DAYS))
    else:
        out["forward_sharpe_ann"] = None
    return out


def run(cache_dir: str, state_path: str, asof: date | None = None) -> dict:
    from quantsys.config import load_config
    from quantsys.core.types import InstrumentKind
    from quantsys.research.bhavcopy import build_panel, close_panel
    from quantsys.research.factors import daily_returns

    asof = asof or date.today()
    sp = Path(state_path)
    state = json.loads(sp.read_text()) if sp.exists() else {}
    if "tracking_start" not in state:
        state["tracking_start"] = asof.isoformat()      # FROZEN at first run
    start = pd.Timestamp(state["tracking_start"])

    need_from = (start - pd.Timedelta(days=TRAIL_DAYS)).date()
    tidy = build_panel(need_from, asof, cache_dir, progress_every=0)
    rets = daily_returns(tidy)
    vol = close_panel(tidy, "volume").reindex(rets.index)
    cfg = load_config("config/base.yaml")
    uni = [u.symbol for u in cfg.universe
           if u.kind == InstrumentKind.EQUITY and u.sector]
    syms = [s for s in uni if s in rets.columns and s in vol.columns]
    rets, vol = rets[syms], vol[syms]

    fwd, evs = forward_record(rets, vol, rets.index, start)
    summ = summarize(fwd)
    fwd_evs = [(s, rets.index[i + 1].date().isoformat())
               for s, i in evs if rets.index[i + 1] >= start]
    state.update(summ)
    state["last_run"] = asof.isoformat()
    state["forward_events"] = len(fwd_evs)
    state["recent_events"] = fwd_evs[-10:]
    sp.write_text(json.dumps(state, indent=2))

    # append a one-line history log for charting the forward curve over time
    log = sp.with_suffix(".log.csv")
    hdr = not log.exists()
    with log.open("a") as fh:
        if hdr:
            fh.write("run_date,tracking_start,forward_events,forward_days,"
                     "forward_cum_return,forward_sharpe_ann\n")
        fh.write(f"{asof.isoformat()},{state['tracking_start']},{len(fwd_evs)},"
                 f"{summ['forward_days']},{summ['forward_cum_return']:.6f},"
                 f"{summ['forward_sharpe_ann']}\n")

    print(f"[downshock-tracker] asof={asof} tracking_since={state['tracking_start']} "
          f"events={len(fwd_evs)} active_days={summ['forward_days']} "
          f"cum_return={summ['forward_cum_return']*100:+.2f}% "
          f"sharpe={summ['forward_sharpe_ann']}")
    if fwd_evs:
        print("  recent forward events:", ", ".join(f"{s}@{d}" for s, d in fwd_evs[-5:]))
    else:
        print("  no forward down-shock events yet — watching.")
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="downshock_state.json")
    ap.add_argument("--cache", default="data_cache/bhavcopy")
    ap.add_argument("--asof", default=None, help="YYYY-MM-DD (default: today)")
    a = ap.parse_args()
    asof = datetime.strptime(a.asof, "%Y-%m-%d").date() if a.asof else None
    run(a.cache, a.state, asof)


if __name__ == "__main__":
    main()
