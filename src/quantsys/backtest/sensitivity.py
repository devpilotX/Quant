"""Parameter-sensitivity sweep — perturb one tunable at a time around the
baseline, re-run, and report how OOS Sharpe/CAGR/maxDD move. A strategy whose
edge survives only at one knob setting is overfit; this exposes that and feeds
``n_trials`` into the deflated-Sharpe deflation (every variant tried counts).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime

from quantsys.backtest.loop import run_backtest
from quantsys.backtest.metrics import compute_metrics
from quantsys.config.schema import AppConfig
from quantsys.core.types import Bar


@dataclass
class SweepPoint:
    param: str
    value: float
    sharpe: float | None
    cagr: float | None
    max_dd: float | None
    n_trades: int


@dataclass
class SensitivityResult:
    points: list[SweepPoint] = field(default_factory=list)
    n_trials: int = 0


def _set_path(cfg: AppConfig, path: str, value) -> None:
    obj = cfg
    parts = path.split(".")
    for p in parts[:-1]:
        obj = getattr(obj, p)
    cur = getattr(obj, parts[-1])
    setattr(obj, parts[-1], type(cur)(value))


def sweep(
    base_cfg: AppConfig,
    bars: list[tuple[datetime, dict[str, Bar]]],
    starting_equity: float,
    grid: dict[str, list[float]],
    warmup_bars: int = 0,
) -> SensitivityResult:
    res = SensitivityResult()
    for param, values in grid.items():
        for v in values:
            cfg = copy.deepcopy(base_cfg)
            try:
                _set_path(cfg, param, v)
            except Exception:
                continue
            r = run_backtest(cfg, iter(bars), starting_equity, warmup_bars=warmup_bars)
            m = compute_metrics(r.daily_equity(), r.trades, r.total_fees,
                                r.traded_notional, starting_equity)
            res.points.append(SweepPoint(
                param=param, value=float(v), sharpe=m.get("sharpe"),
                cagr=m.get("cagr"), max_dd=m.get("max_dd"),
                n_trades=m.get("n_trades", 0),
            ))
            res.n_trials += 1
    return res
