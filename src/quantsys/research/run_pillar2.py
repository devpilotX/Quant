"""Pillar-2 honest validation harness (pre-registered, anti-overfit).

Protocol (fixed BEFORE seeing any hold-out number, mirroring the prior
MAX_POTENTIAL_AUDIT discipline):

  * Grid of `N` factor configs (factor x side x top_k x universe size).
  * In-sample window  = up to 2023-12-31  (selection only).
  * Hold-out window   = 2024-01-01 onward (evaluated ONCE for the winner).
  * Selection rule    = best IS annualised Sharpe among configs with >= 24
                        rebalances. Chosen before any hold-out is read.
  * Deflated Sharpe   trial count = N (every config tried).
  * PBO (CSCV)        computed across the whole grid on monthly returns.
  * Purged K-fold     Sharpe stability on the winner's full daily series.
  * Bar to beat       = the gate (OOS Sharpe>=0.8, deflated>=0.95, P(SR<0)<=0.10)
                        AND equal-weight buy-and-hold of the same universe.

Run:  python -m quantsys.research.run_pillar2 [panel.parquet]
"""

from __future__ import annotations

import json
import sys
import time

import pandas as pd

from quantsys.backtest.metrics import compute_metrics, deflated_sharpe, monte_carlo_resample
from quantsys.config.schema import CostConfig
from quantsys.costs import CostModel
from quantsys.research import validation as V
from quantsys.research.xs_backtest import BTConfig, prepare_matrices, run_xs_backtest

IS_END = "2023-12-31"
OOS_START = "2024-01-01"

FACTOR_SETS = [("momentum",), ("lowvol",), ("reversal",), ("illiquidity",), ("momentum", "lowvol")]
SIDES = ["long_only", "long_short"]
TOPK = [20, 30, 50]
TOPN = [100, 200]


def _grid() -> list[BTConfig]:
    out = []
    for f in FACTOR_SETS:
        for s in SIDES:
            for k in TOPK:
                for n in TOPN:
                    if k * 2 > n:           # need top_k longs + top_k shorts within universe
                        continue
                    out.append(BTConfig(factor=f, side=s, top_k=k, top_n_universe=n))
    return out


def _slice_metrics(equity: pd.Series, lo=None, hi=None) -> dict:
    e = equity
    if lo:
        e = e[e.index >= pd.Timestamp(lo)]
    if hi:
        e = e[e.index <= pd.Timestamp(hi)]
    if len(e) < 5:
        return {}
    de = [(d, v) for d, v in zip(e.index, e.values)]
    return compute_metrics(de, [], 0.0, 0.0, float(e.iloc[0]))


def _monthly_returns(equity: pd.Series) -> pd.Series:
    return equity.resample("ME").last().pct_change().dropna()


def main(panel_path: str = "data_cache/bhavcopy/panel_2016_2026.parquet") -> dict:
    panel = pd.read_parquet(panel_path)
    panel["date"] = pd.to_datetime(panel["date"])
    print(f"panel: {panel.shape[0]:,} rows, {panel.symbol.nunique():,} symbols, "
          f"{str(panel.date.min())[:10]}..{str(panel.date.max())[:10]}")
    cm = CostModel(CostConfig())
    P = prepare_matrices(panel)

    grid = _grid()
    N = len(grid)
    print(f"grid: {N} configs; deflating at n_trials={N}\n")

    rows = []
    monthly = {}
    for i, cfg in enumerate(grid):
        t0 = time.time()
        res = run_xs_backtest(panel, cfg, cost_model=cm, n_trials=N, prepared=P)
        if res.equity.empty:
            continue
        name = f"{'+'.join(cfg.factor)}|{cfg.side}|K{cfg.top_k}|N{cfg.top_n_universe}"
        is_m = _slice_metrics(res.equity, hi=IS_END)
        oos_m = _slice_metrics(res.equity, lo=OOS_START)
        rows.append({
            "name": name, "cfg": cfg, "res": res,
            "is_sharpe": is_m.get("sharpe"), "is_cagr": is_m.get("cagr"),
            "oos_sharpe": oos_m.get("sharpe"), "oos_cagr": oos_m.get("cagr"),
            "oos_maxdd": oos_m.get("max_dd"), "n_reb": res.n_rebalances,
            "beta": res.realized_beta,
        })
        monthly[name] = _monthly_returns(res.equity)
        print(f"  [{i+1:>2}/{N}] {name:38} IS Sh {is_m.get('sharpe'):>6.2f}  "
              f"OOS Sh {oos_m.get('sharpe'):>6.2f}  ({time.time()-t0:.1f}s)")

    # ---- PBO across the grid (monthly returns aligned) ----
    md = pd.DataFrame(monthly).dropna(how="all")
    md = md.dropna(axis=1)  # configs with full monthly coverage
    pbo = V.pbo_cscv(md.values, n_splits=10) if md.shape[1] >= 2 else {"pbo": None}

    # ---- selection: best IS Sharpe with >= 24 rebalances ----
    eligible = [r for r in rows if r["n_reb"] >= 24 and r["is_sharpe"] is not None]
    winner = max(eligible, key=lambda r: r["is_sharpe"])
    w = winner["res"]
    w_oos = _slice_metrics(w.equity, lo=OOS_START)
    w_full_daily = w.equity.pct_change().dropna()
    deflated = deflated_sharpe(w_oos.get("sharpe"), w_oos.get("n_days", 0),
                               w_oos.get("skew", 0.0), w_oos.get("kurtosis", 3.0), N)
    mc = monte_carlo_resample([(d, v) for d, v in zip(
        w.equity[w.equity.index >= pd.Timestamp(OOS_START)].index,
        w.equity[w.equity.index >= pd.Timestamp(OOS_START)].values)])
    cv = V.cv_sharpe_stability(w_full_daily.values, n_splits=5)
    bench_oos = _slice_metrics(w.bench, lo=OOS_START)

    print("\n" + "=" * 78)
    print(f"WINNER (by IS Sharpe): {winner['name']}")
    print(f"  IS  : Sharpe {winner['is_sharpe']:.2f}  CAGR {winner['is_cagr']*100:.1f}%")
    print(f"  OOS : Sharpe {w_oos.get('sharpe'):.2f}  CAGR {w_oos.get('cagr')*100:.1f}%  "
          f"maxDD {w_oos.get('max_dd')*100:.1f}%  beta {winner['beta']}")
    print(f"  OOS deflated Sharpe (n_trials={N}): {deflated:.3f}   [gate needs >= 0.95]")
    print(f"  OOS P(SR<0) MonteCarlo: {mc.get('p_sharpe_negative')}   [gate needs <= 0.10]")
    print(f"  Purged 5-fold Sharpe stability (full): {cv}")
    print(f"  Buy&Hold (same universe) OOS: Sharpe {bench_oos.get('sharpe'):.2f}  "
          f"CAGR {bench_oos.get('cagr')*100:.1f}%")
    print(f"  PBO across grid: {pbo.get('pbo')}   [near 0 good; near 1 overfit]")
    print("=" * 78)

    # sorted leaderboard by IS Sharpe (top 12)
    print("\nLeaderboard (top 12 by IS Sharpe) — IS->OOS decay:")
    for r in sorted(eligible, key=lambda r: -r["is_sharpe"])[:12]:
        print(f"  {r['name']:38} IS {r['is_sharpe']:>6.2f} -> OOS {r['oos_sharpe']:>6.2f}  "
              f"| OOS CAGR {r['oos_cagr']*100:>6.1f}%  beta {('%.2f'%r['beta']) if r['beta'] else 'na'}")

    summary = {
        "n_configs": N, "pbo": pbo.get("pbo"),
        "winner": winner["name"],
        "is_sharpe": winner["is_sharpe"], "is_cagr": winner["is_cagr"],
        "oos_sharpe": w_oos.get("sharpe"), "oos_cagr": w_oos.get("cagr"),
        "oos_maxdd": w_oos.get("max_dd"), "oos_beta": winner["beta"],
        "oos_deflated": deflated, "oos_p_sr_neg": mc.get("p_sharpe_negative"),
        "purged_cv": cv, "bench_oos_sharpe": bench_oos.get("sharpe"),
        "bench_oos_cagr": bench_oos.get("cagr"),
        "leaderboard": [
            {"name": r["name"], "is_sharpe": r["is_sharpe"], "oos_sharpe": r["oos_sharpe"],
             "oos_cagr": r["oos_cagr"], "beta": r["beta"]}
            for r in sorted(eligible, key=lambda r: -r["is_sharpe"])
        ],
    }
    with open("data_cache/pillar2_results.json", "w") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print("\nwrote data_cache/pillar2_results.json")
    return summary


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
