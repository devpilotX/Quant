"""CLI: run a full walk-forward study and (optionally) persist it to the
dashboard's backtest_runs table.

    python -m quantsys.backtest.runstudy --synthetic --bars 6000 --capital 50000000
    python -m quantsys.backtest.runstudy --replay data/nse --persist

Honest-reporting contract: prints IS vs OOS, deflated Sharpe with the real
n_trials from the sweep, and Monte-Carlo tails. Synthetic data is labelled as
machine-validation only, never edge evidence.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

# force UTF-8 stdout so report glyphs survive Windows cp1252 consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from quantsys.backtest.loop import run_backtest
from quantsys.backtest.metrics import compute_metrics, deflated_sharpe, monte_carlo_resample
from quantsys.backtest.sensitivity import sweep
from quantsys.backtest.synth import replay_bars, synthetic_bars
from quantsys.backtest.walkforward import walk_forward
from quantsys.config import load_config


def _fmt(m: dict, *keys: str) -> str:
    out = []
    for k in keys:
        v = m.get(k)
        if v is None:
            out.append(f"{k}=—")
        elif isinstance(v, float):
            out.append(f"{k}={v:.4g}")
        else:
            out.append(f"{k}={v}")
    return "  ".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/base.yaml")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--replay", default=None)
    ap.add_argument("--bars", type=int, default=6000)
    ap.add_argument("--capital", type=float, default=50_000_000)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--persist", action="store_true",
                    help="write to backtest_runs (needs qsdash + DB)")
    ap.add_argument("--quick", action="store_true",
                    help="smaller sweep grid (faster; lower n_trials)")
    ap.add_argument("--max-bars", type=int, default=0,
                    help="replay: cap to the most recent N merged bars (0=all). "
                         "Intraday HMM refits make the full multi-year 5-min "
                         "history impractically slow; bound it here.")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    cfg = load_config(args.config)
    syms = [u.symbol for u in cfg.universe]
    bar_min = cfg.engine.decision_bar_minutes
    is_synth = not args.replay

    if args.replay:
        bars = list(replay_bars(args.replay, syms))
        if args.max_bars and len(bars) > args.max_bars:
            bars = bars[-args.max_bars:]
        source = f"replay:{args.replay}"
    else:
        bars = list(synthetic_bars(syms, datetime(2026, 1, 1) - timedelta(days=0),
                                   args.bars, bar_min, seed=args.seed))
        source = "synthetic"

    print("# quantsys backtest study")
    print(f"source={source} bars={len(bars)} symbols={len(syms)} "
          f"capital={args.capital:,.0f} bar_minutes={bar_min}")
    if is_synth:
        print("!! SYNTHETIC DATA — validates the machine (accounting, costs, "
              "no-lookahead), NOT edge. Edge claims require real NSE history.")

    warmup = min(len(bars) // 4, 500)

    # full-sample run (for the dashboard curve + Monte Carlo)
    full = run_backtest(cfg, iter(bars), args.capital, warmup_bars=warmup)
    full_m = compute_metrics(full.daily_equity(), full.trades, full.total_fees,
                             full.traded_notional, args.capital,
                             [s.gross for s in full.equity_curve],
                             [s.equity for s in full.equity_curve])

    # walk-forward IS vs OOS
    wf = walk_forward(cfg, bars, args.capital, n_folds=args.folds, train_frac=0.5)

    # sensitivity sweep -> n_trials for deflation
    if args.quick:
        grid = {"sizing.base_risk_frac": [0.005, 0.0075]}
    else:
        grid = {
            "sizing.base_risk_frac": [0.003, 0.005, 0.0075],
            "vol_target.annual_vol_target": [0.10, 0.13, 0.16],
        }
    sw = sweep(cfg, bars, args.capital, grid, warmup_bars=warmup)
    n_trials = max(1, sw.n_trials)

    oos = wf.combined_oos
    dsr = deflated_sharpe(oos.get("sharpe"), oos.get("n_days", 0) * 1,
                          skew=oos.get("skew", 0.0),
                          kurtosis=oos.get("kurtosis", 3.0),
                          n_trials=n_trials)
    mc = monte_carlo_resample(
        [(datetime(d.year, d.month, d.day), v)
         for d, v in {s.ts.date(): s.equity for s in wf.oos_curve}.items()],
        n_paths=1000)

    print("\n## Full-sample")
    print(_fmt(full_m, "cagr", "sharpe", "sortino", "calmar", "max_dd",
               "time_in_dd", "n_trades", "hit_rate", "profit_factor",
               "cost_drag_bps", "turnover_x_per_year"))
    print("\n## Walk-forward (pooled IS vs combined OOS)")
    print("IS  ", _fmt(wf.pooled_is, "cagr", "sharpe", "max_dd"))
    print("OOS ", _fmt(oos, "cagr", "sharpe", "sortino", "max_dd", "n_trades",
                       "hit_rate", "cost_drag_bps"))
    if wf.pooled_is.get("sharpe") and oos.get("sharpe"):
        deg = wf.pooled_is["sharpe"] - oos["sharpe"]
        print(f"IS->OOS Sharpe degradation: {deg:+.2f}")
    print(f"deflated Sharpe (n_trials={n_trials}): "
          f"{dsr:.3f}" if dsr is not None else "deflated Sharpe: —")
    print("\n## Per fold (OOS)")
    for f in wf.folds:
        print(f"  fold {f.index}: {f.test_start.date()}→{f.test_end.date()}  "
              + _fmt(f.oos_metrics, "sharpe", "cagr", "max_dd", "n_trades"))
    print("\n## Monte-Carlo (block bootstrap, OOS returns)")
    print("  ", _fmt(mc, "sharpe_p05", "sharpe_p50", "sharpe_p95",
                     "p_sharpe_negative", "max_dd_p95"))

    verdict = _verdict(is_synth, oos, dsr, mc)
    print("\n## VERDICT\n" + verdict)

    if args.persist:
        _persist(args, source, cfg, full, full_m, wf, oos, dsr, mc, n_trials, verdict)


def _verdict(is_synth: bool, oos: dict, dsr: float | None, mc: dict) -> str:
    if is_synth:
        return ("Synthetic run: machine validated (accounting identity held, "
                "costs charged, no look-ahead, deterministic). NOT a go-live "
                "signal — wire the historical NSE fetcher and re-run before "
                "trusting any edge number. The GO-LIVE gate stays CLOSED.")
    if oos.get("sharpe") is None:
        return "Insufficient OOS data to judge. Gate CLOSED."
    sharpe = oos["sharpe"]
    p_neg = mc.get("p_sharpe_negative", 1.0)
    ok = (sharpe > 0.8 and (dsr is not None and dsr > 0.95)
          and p_neg < 0.10 and oos.get("cost_drag_bps", 1e9) < sharpe * 1e9)
    if ok:
        return (f"OOS Sharpe {sharpe:.2f}, deflated p={dsr:.3f}, "
                f"P(SR<0)={p_neg:.2f}: edge is positive and robust after costs. "
                "Eligible for tiny-capital go-live via the dashboard safety chain.")
    return (f"OOS Sharpe {sharpe:.2f}, deflated p={dsr}, P(SR<0)={p_neg:.2f}: "
            "NOT robust after costs/deflation. Do NOT go live. Fix or cut the "
            "weak strategies first.")


def _persist(args, source, cfg, full, full_m, wf, oos, dsr, mc, n_trials, verdict):
    import subprocess

    from qsdash.db import SessionLocal
    from qsdash.models import BacktestRun

    try:
        rev = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                      text=True).strip()
    except Exception:
        rev = ""
    curve = [[s.ts.isoformat(), round(s.equity, 2)] for s in full.equity_curve[::max(1, len(full.equity_curve)//3000)]]
    metrics = {
        "source": source, "is_synthetic": bool(args.synthetic),
        "full": full_m, "oos": oos, "is": wf.pooled_is,
        "sharpe_oos": oos.get("sharpe"), "sharpe_deflated": dsr,
        "max_dd": oos.get("max_dd"), "monte_carlo": mc,
        "n_trials": n_trials, "verdict": verdict,
        "folds": [{"index": f.index, "test_start": f.test_start.isoformat(),
                   "test_end": f.test_end.isoformat(),
                   "oos": f.oos_metrics, "is": {k: v for k, v in f.is_metrics.items()
                                                if k != "_daily"}}
                  for f in wf.folds],
    }
    s = SessionLocal()
    try:
        run = BacktestRun(
            label=args.label or f"{source} {datetime.now():%Y-%m-%d %H:%M}",
            git_rev=rev, params={"capital": args.capital, "folds": args.folds,
                                 "bars": len(curve), "config": args.config},
            metrics=metrics, equity_curve=curve,
            artifacts={"warmup_bars": full.warmup_bars},
        )
        s.add(run)
        s.commit()
        print(f"\npersisted backtest_runs id={run.id}")
    finally:
        s.close()


if __name__ == "__main__":
    main()
