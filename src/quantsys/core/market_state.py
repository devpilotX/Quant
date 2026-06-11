"""MarketState: the single read-only snapshot every strategy and risk
component consumes. Built identically by the backtester and the live data
layer — this is what makes 'same code in backtest and live' true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from quantsys.core.types import Instrument, Position
from quantsys.data.history import BarHistory


@dataclass
class MarketState:
    ts: datetime
    equity: float
    bars: Mapping[str, BarHistory]
    instruments: Mapping[str, Instrument]
    positions: Mapping[str, Position] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. india_vix

    def price(self, symbol: str) -> float:
        hist = self.bars.get(symbol)
        return hist.last_close if hist is not None and len(hist) else float("nan")

    def n_bars(self, symbol: str) -> int:
        hist = self.bars.get(symbol)
        return len(hist) if hist is not None else 0

    def restricted(self, symbols: set[str]) -> "MarketState":
        """View limited to a universe subset (tier gating). Cheap: shares arrays."""
        return MarketState(
            ts=self.ts,
            equity=self.equity,
            bars={s: h for s, h in self.bars.items() if s in symbols},
            instruments={s: i for s, i in self.instruments.items() if s in symbols},
            positions=self.positions,
            extra=self.extra,
        )
