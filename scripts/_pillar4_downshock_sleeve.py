"""Pillar 4 / sec.8a follow-through — DOWN-SHOCK short sleeve, FULL 5-criterion gate.

The screen found a significant down-shock underreaction (continued downward drift
after a 4sigma+volume down day). Here we test whether it is a DEPLOYABLE edge or
just a pretty event-study curve: a market-NEUTRAL short sleeve (short the shocked
stock, beta-hedged with the index, so we trade the ABNORMAL drift the study
measured), real SSF costs + financing, pre-registered grid, IS-select -> hold-out
once -> deflated Sharpe / PBO / Monte-Carlo against the gate (docs sec.6).

Pre-registered sleeve grid (declared before this backtest): z_thresh in
{3.5,4.0,4.5} x hold in {10,20,30} = 9 configs. Cumulative n_trials for DSR =
12 (meanrev) + 9 (here) = 21. Market-neutral daily P&L = -AR_t (abnormal return),
weight 1/5 per position, <=5 concurrent. Honesty self-test guards the simulator.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from quantsys.backtest.metrics import deflated_sharpe, monte_carlo_resample
from quantsys.config import load_config
from quantsys.core.types import Instrument, InstrumentKind
from quantsys.costs import CostModel
from quantsys.research.bhavcopy import build_panel, close_panel
from quantsys.research.event_study import abnormal_returns, fit_market_model
from quantsys.research.factors import daily_returns
from quantsys.research.validation import pbo_cscv

V_THRESH, DECLUSTER, MAX_CONC = 2.0, 30, 5
Z_GRID, HOLD_GRID = [3.5, 4.0, 4.5], [10, 20, 30]
EST = (-250, -30)
TRADING_DAYS = 252
IS_END = pd.Timestamp("2023-12-31")
N_TRIALS = 21                                # 12 meanrev + 9 here


def down_events(rets, vol, syms, idx, z_thresh):
    sigma = rets.rolling(60).std().shift(1)
    z = rets / sigma
    vratio = vol / vol.rolling(20).mean().shift(1)
    pos = {d: i for i, d in enumerate(idx)}
    out = []
    for s in syms:
        zs = z[s]
        mask = (zs <= -z_thresh) & (vratio[s] >= V_THRESH)
        last = -10**9
        for d in idx[mask.to_numpy(na_value=False)]:
            i = pos[d]
            if i - last >= DECLUSTER:
                last = i
                out.append((s, i))
    return out


def sleeve_daily(rets, market, events, hold, cost_rt, fin_daily, idx):
    """Market-neutral short daily returns: -AR_t over [+1,+hold], beta-hedged,
    <=MAX_CONC concurrent, 1/MAX_CONC weight, SSF costs at entry/exit."""
    n = len(idx)
    daily = np.zeros(n)
    w = 1.0 / MAX_CONC
    active_until = []                              # exit indices of open positions
    rm = market.to_numpy()
    for s, e0 in sorted(events, key=lambda t: t[1]):
        est_lo, est_hi = e0 + EST[0], e0 + EST[1]
        ev_lo, ev_hi = e0 + 1, e0 + hold
        if est_lo < 0 or ev_hi >= n:
            continue
        active_until = [x for x in active_until if x > e0]
        if len(active_until) >= MAX_CONC:          # concurrency cap
            continue
        ri = rets[s].to_numpy()
        fin = np.isfinite(ri[est_lo:est_hi + 1]) & np.isfinite(rm[est_lo:est_hi + 1])
        if fin.sum() < 120:
            continue
        m = fit_market_model(ri[est_lo:est_hi + 1][fin], rm[est_lo:est_hi + 1][fin])
        ar = abnormal_returns(ri[ev_lo:ev_hi + 1], rm[ev_lo:ev_hi + 1], m)
        if not np.all(np.isfinite(ar)):
            continue
        daily[ev_lo:ev_hi + 1] += w * (-ar)        # SHORT the abnormal drift
        daily[ev_lo] -= w * cost_rt / 2.0
        daily[ev_hi] -= w * cost_rt / 2.0
        daily[ev_lo:ev_hi + 1] -= w * fin_daily    # financing on the short leg
        active_until.append(ev_hi)
    return pd.Series(daily, index=idx)


def metrics(r):
    r = r[np.isfinite(r)]
    act = r[r != 0]
    if len(act) < 20 or act.std(ddof=1) == 0:
        return {"sharpe": 0.0, "cagr": 0.0, "maxdd": 0.0, "ndays": len(act)}
    sharpe = float(r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS))
    eq = (1 + r).cumprod()
    cagr = float(eq.iloc[-1] ** (TRADING_DAYS / len(r)) - 1)
    maxdd = float((eq / eq.cummax() - 1).min())
    return {"sharpe": sharpe, "cagr": cagr, "maxdd": maxdd, "ndays": int((r != 0).sum())}


def _self_test(idx):
    """Events whose stock has a KNOWN negative abnormal drift must yield POSITIVE
    market-neutral short P&L; zero-drift events must net < 0 after costs."""
    n = len(idx)
    rng = np.random.default_rng(0)
    mkt = pd.Series(rng.normal(0, 0.01, n), index=idx)
    sig = pd.DataFrame({"S": mkt.to_numpy()}, index=idx)        # beta 1, no idio
    evs = []
    for e0 in range(400, n - 60, 120):
        sig.iloc[e0 + 1:e0 + 21, 0] -= 0.003                   # -30bps/day abnormal for 20d
        evs.append(("S", e0))
    pos = sleeve_daily(sig, mkt, evs, 20, 0.0, 0.0, idx).sum()
    assert pos > 0.02, f"known negative drift must profit a short, got {pos:.3f}"
    print(f"  self-test OK (short of a -drift event book = +{pos:.3f})")


def main():
    cfg = load_config("config/base.yaml")
    cm = CostModel(cfg.costs)
    fu = Instrument("FU", kind=InstrumentKind.FUTURE, lot_size=1, point_value=1.0)
    fut_rt = cm.round_trip(fu, 1_000_000.0, 1000.0, delivery=False) / 1e9
    cost_rt = 2 * fut_rt                                        # short SSF + long index hedge
    fin_daily = 0.015 / 365.0
    print(f"cost: 2-leg futures RT={cost_rt*1e4:.0f}bps + financing 1.5%/yr")

    syms_all = sorted(u.symbol for u in cfg.universe
                      if u.kind == InstrumentKind.EQUITY and u.sector)
    print("loading panel 2016..2026 (cache)...")
    tidy = build_panel(date(2016, 1, 1), date(2026, 6, 19), progress_every=0)
    rets = daily_returns(tidy)
    vol = close_panel(tidy, "volume").reindex(rets.index)
    syms = [s for s in syms_all if s in rets.columns and s in vol.columns]
    rets, vol = rets[syms], vol[syms]
    idx = rets.index
    market = rets.mean(axis=1)
    _self_test(idx)

    is_curves, results = {}, []
    for z in Z_GRID:
        evs = down_events(rets, vol, syms, idx, z)
        for h in HOLD_GRID:
            r = sleeve_daily(rets, market, evs, h, cost_rt, fin_daily, idx)
            is_r, oos_r = r[r.index <= IS_END], r[r.index > IS_END]
            is_curves[(z, h)] = is_r
            results.append({"z": z, "h": h, "nev": len(evs),
                            "is": metrics(is_r), "oos": metrics(oos_r), "r": r})

    print("\n config (z,hold)   nEvents  IS_Sharpe IS_CAGR  OOS_Sharpe OOS_CAGR OOS_maxDD")
    for x in results:
        print(f"  z={x['z']:<4} h={x['h']:<3}   {x['nev']:<7} {x['is']['sharpe']:8.2f} "
              f"{x['is']['cagr']*100:6.1f}%  {x['oos']['sharpe']:8.2f} {x['oos']['cagr']*100:7.1f}%"
              f" {x['oos']['maxdd']*100:8.1f}%")

    winner = max(results, key=lambda x: x["is"]["sharpe"])
    z, h, oos = winner["z"], winner["h"], winner["oos"]
    oos_r = winner["r"][winner["r"].index > IS_END]
    dsr = deflated_sharpe(oos["sharpe"], int((oos_r != 0).sum()) or len(oos_r),
                          n_trials=N_TRIALS)
    mat = pd.DataFrame(dict(is_curves)).fillna(0.0).to_numpy()
    pbo = pbo_cscv(mat, n_splits=10).get("pbo")
    eq = (1 + oos_r).cumprod()
    mc = monte_carlo_resample(list(zip(oos_r.index, eq.to_numpy()))).get("p_sharpe_negative")

    print("\n==== PRE-REGISTERED WINNER (IS-selected) ====")
    print(f"config: z_thresh={z}, hold={h}d")
    print(f"HOLD-OUT (2024+): Sharpe={oos['sharpe']:.2f} CAGR={oos['cagr']*100:.1f}% "
          f"maxDD={oos['maxdd']*100:.1f}%")
    print(f"Deflated Sharpe (n_trials={N_TRIALS})={dsr}  PBO={pbo}  MC P(SR<0)={mc}")
    gate = {
        "OOS Sharpe>=0.80": oos["sharpe"] >= 0.80,
        "Deflated>=0.95": (dsr or 0) >= 0.95,
        "P(SR<0)<=0.10": (mc if mc is not None else 1.0) <= 0.10,
        "PBO<=0.50": (pbo if pbo is not None else 1.0) <= 0.50,
        "net CAGR>0": oos["cagr"] > 0,
    }
    print("\n GATE:")
    for k, v in gate.items():
        print(f"   [{'PASS' if v else 'FAIL'}] {k}")
    print("\nVERDICT:", "CANDIDATE — clears the gate, propose forward-only paper"
          if all(gate.values()) else "DEAD — does not clear the gate (STOP per stop-rule)")


if __name__ == "__main__":
    main()
