"""Event-driven backtester.

The backtester drives the SAME DecisionEngine.decide() path the live runner
uses — no re-implemented strategy/sizing/risk logic anywhere. Fills are
simulated by SimBroker at bar close with the engine's own CostModel, so
backtest economics and the live cost gate share one fee schedule.
"""

from quantsys.backtest.loop import BacktestResult, run_backtest
from quantsys.backtest.metrics import compute_metrics, deflated_sharpe
from quantsys.backtest.simbroker import SimBroker
from quantsys.backtest.synth import synthetic_bars
from quantsys.backtest.walkforward import walk_forward

__all__ = [
    "BacktestResult",
    "SimBroker",
    "compute_metrics",
    "deflated_sharpe",
    "run_backtest",
    "synthetic_bars",
    "walk_forward",
]
