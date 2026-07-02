"""Turn-of-month index tilt (calendar event sleeve).

NEW pre-registered hypothesis for Forward Study 2: institutional month-end
flows tilt index returns positive around the month boundary. Rule: LONG the
configured index futures from the last `days_before` weekdays of a month
through the first `days_after` weekdays of the next; flat otherwise. The
window is a pure deterministic function of the bar timestamp (weekday
approximation of session days — holidays shift the true boundary by at most a
day, accepted and disclosed). Stops are ATR on ~daily resampled decision bars.

Unlike the panel sleeves this one needs no seeding, so it also trades in the
intraday backtest.
"""

from __future__ import annotations

import calendar
import math
from datetime import date

from quantsys.config.schema import TomConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import Signal
from quantsys.data.features import atr
from quantsys.strategies.base import Strategy, register


def _weekdays_left(d: date) -> int:
    """Weekdays from d (inclusive) to month end."""
    last = calendar.monthrange(d.year, d.month)[1]
    return sum(1 for day in range(d.day, last + 1)
               if date(d.year, d.month, day).weekday() < 5)

def _weekday_index(d: date) -> int:
    """1-based weekday index of d within its month (0 if d is a weekend)."""
    if d.weekday() >= 5:
        return 0
    return sum(1 for day in range(1, d.day + 1)
               if date(d.year, d.month, day).weekday() < 5)


def tom_window_active(d: date, days_before: int, days_after: int) -> bool:
    """True inside the (-days_before, +days_after) weekday window around the
    month turn."""
    if d.weekday() >= 5:
        return False
    return (_weekdays_left(d) <= days_before
            or 0 < _weekday_index(d) <= days_after)


@register("tom")
class TomStrategy(Strategy):
    def __init__(self, cfg: TomConfig):
        super().__init__("tom")
        self.cfg = cfg

    def warmup_bars(self) -> int:
        return self.cfg.timeframe_bars * (self.cfg.atr_n + 3)

    def generate_signals(self, state: MarketState) -> list[Signal]:
        cfg = self.cfg
        if not tom_window_active(state.ts.date(), cfg.days_before, cfg.days_after):
            return []
        out: list[Signal] = []
        for sym in cfg.symbols:
            if sym not in state.bars:
                continue
            rs = state.bars[sym].resampled(cfg.timeframe_bars)
            if not rs or len(rs["close"]) < cfg.atr_n + 2:
                continue
            a = atr(rs["high"], rs["low"], rs["close"], cfg.atr_n)
            if not (math.isfinite(a) and a > 0):
                continue
            out.append(Signal(
                strategy=self.name, symbol=sym, direction=1.0,
                stop_distance=cfg.atr_mult * a,
                expected_edge_R=cfg.expected_edge_R, tag=sym,
            ))
        return out
