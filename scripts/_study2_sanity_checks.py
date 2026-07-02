"""Forward Study 2 — pre-deployment sanity checks on cached daily data.

REPORTING, not selection: the deployed configs are frozen in config/base.yaml
regardless of these numbers (reversal 5d/8, tom -2/+3). This script documents
(a) what the frozen configs would have done historically net of costs, (b) the
sensitivity neighborhood with honest multiple-testing accounting, and (c) the
turn-of-month effect's presence on our universe — so the Study-2 registration
states its priors with evidence attached, exactly like the closeout demanded.

Data: data_cache/bhavcopy/panel_2016_2026.parquet (point-in-time, free NSE).
Zero network. Down-shock is NOT re-run here: its full gate result is frozen in
docs/PILLAR4_EVENT_DRIVEN.md (re-running would be re-search).

Run:  python scripts/_study2_sanity_checks.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quantsys.backtest.metrics import deflated_sharpe  # noqa: E402
from quantsys.config import load_config  # noqa: E402
from quantsys.research.validation import pbo_cscv  # noqa: E402
from quantsys.strategies.tom import tom_window_active  # noqa: E402

PANEL = ROOT / "data_cache" / "bhavcopy" / "panel_2016_2026.parquet"
RT_COST = 0.0028          # delivery round-trip ~28 bps (research convention)
START = date(2017, 1, 3)  # skip the seed year edge


def _closes() -> pd.DataFrame:
    cfg = load_config(str(ROOT / "config" / "base.yaml"))
    universe = [u.symbol for u in cfg.universe if u.kind.value == "EQUITY"]
    tidy = pd.read_parquet(PANEL, columns=["date", "symbol", "close"])
    tidy = tidy[tidy.symbol.isin(universe)]
    px = tidy.pivot_table(index="date", columns="symbol", values="close")
    px = px[px.index.date >= START].sort_index()
    return px


def reversal_backtest(px: pd.DataFrame, lookback: int, k: int) -> np.ndarray:
    """Weekly-rebalanced k-long/k-short reversal, equal-weight, net of costs.
    Returns the daily portfolio return series."""
    rets = px.pct_change()
    out = np.zeros(len(px))
    w = pd.Series(0.0, index=px.columns)
    turnover_cost = 0.0
    for i in range(lookback + 1, len(px)):
        out[i] = float((w * rets.iloc[i]).sum()) - turnover_cost
        turnover_cost = 0.0
        if (i - lookback - 1) % 5 == 0:                    # weekly re-rank
            window = px.iloc[i - lookback: i + 1]
            r = (window.iloc[-1] / window.iloc[0] - 1.0).dropna()
            if len(r) < 34:                                 # breadth gate
                new = pd.Series(0.0, index=px.columns)
            else:
                ranked = r.sort_values()
                new = pd.Series(0.0, index=px.columns)
                new[ranked.index[:k]] = 1.0 / (2 * k)       # losers long
                new[ranked.index[-k:]] = -1.0 / (2 * k)     # winners short
            turnover_cost = float((new - w).abs().sum()) * RT_COST / 2.0
            w = new
    return out[lookback + 2:]


def _sharpe(daily: np.ndarray) -> float:
    sd = daily.std()
    return float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0


def main() -> None:
    px = _closes()
    lines: list[str] = []
    p = lines.append
    p("# Study 2 sanity checks — %s" % date.today().isoformat())
    p("")
    p("Panel: %d sessions x %d config equities (%s..%s), net %d bps RT delivery."
      % (len(px), px.shape[1], px.index[0].date(), px.index[-1].date(),
         RT_COST * 1e4))
    p("")

    # ---------------------------------------------------------- reversal
    p("## Short-term reversal (frozen: lookback 5d, k=8, weekly)")
    p("")
    p("| lookback | k | net Sharpe | net CAGR | frozen |")
    p("|---|---|---|---|---|")
    grid, frozen_daily = {}, None
    for lb in (3, 5, 10):
        for k in (6, 8, 10):
            daily = reversal_backtest(px, lb, k)
            grid[(lb, k)] = daily
            cagr = (np.prod(1 + daily) ** (252 / len(daily)) - 1) * 100
            mark = " <-- deployed" if (lb, k) == (5, 8) else ""
            if (lb, k) == (5, 8):
                frozen_daily = daily
            p("| %d | %d | %.2f | %+.1f%% | %s |" % (lb, k, _sharpe(daily),
                                                     cagr, mark))
    n = min(len(v) for v in grid.values())
    matrix = np.column_stack([v[-n:] for v in grid.values()])
    pbo = pbo_cscv(matrix, n_splits=8)["pbo"]
    sr = _sharpe(frozen_daily)
    dsr = deflated_sharpe(sr, n_obs=len(frozen_daily), n_trials=9) or 0.0
    p("")
    p("Frozen cell: Sharpe %.2f, deflated (n_trials=9) %.3f, grid PBO %.2f."
      % (sr, dsr, pbo))
    p("")

    # --------------------------------------------------------------- TOM
    p("## Turn-of-month (frozen: -2/+3 weekdays, long index)")
    p("")
    ew = px.pct_change().mean(axis=1).dropna()          # equal-weight proxy
    mask = np.array([tom_window_active(d.date(), 2, 3) for d in ew.index])
    inside, outside = ew[mask], ew[~mask]
    t = (inside.mean() - outside.mean()) / np.sqrt(
        inside.var() / len(inside) + outside.var() / len(outside))
    strat = ew.copy()
    strat[~mask] = 0.0
    p("in-window mean %+.1f bps/d (n=%d) vs outside %+.1f bps/d (n=%d), "
      "t=%.2f; long-in-window-only Sharpe %.2f (costs ~2 bps RT ignored)."
      % (inside.mean() * 1e4, len(inside), outside.mean() * 1e4, len(outside),
         t, _sharpe(strat.values)))
    p("")
    p("## Down-shock (frozen z3.5/hold10)")
    p("")
    p("Not re-run (stop-rule): full-gate record stands in "
      "docs/PILLAR4_EVENT_DRIVEN.md — hold-out Sharpe 1.75, deflated 0.46 "
      "(FAILED gate), PBO 0.31, MC P(SR<0) 0.002. Forward evidence only.")

    report = "\n".join(lines)
    print(report)
    out = ROOT / "reports" / ("study2_sanity_%s.md" % date.today().isoformat())
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("\nsaved -> %s" % out)


if __name__ == "__main__":
    main()
