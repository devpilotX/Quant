"""Short-term cross-sectional reversal sleeve (daily panel, weekly cadence).

NEW pre-registered hypothesis for Forward Study 2 — no historical validation
was run on our data (the forward paper record IS the test): long the past
`lookback_days` losers, short the winners, dollar-neutral, re-ranked every
`rebalance_days` session days. Declarative targets like factor: a signal is
emitted every bar while a name is wanted; ceasing to emit is the exit. Stops
are daily-ATR based. Breadth-gated (`min_universe`) and unseeded-safe, so it
is a no-op in the intraday backtest — identical precedent to factor.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from quantsys.config.schema import ReversalConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import Signal
from quantsys.strategies._dailypanel import DailyPanelStrategy, atr_from_rows
from quantsys.strategies.base import register


@register("reversal")
class ReversalStrategy(DailyPanelStrategy):
    def __init__(self, cfg: ReversalConfig):
        depth = max(cfg.lookback_days + 2, cfg.atr_n + 2) + 10
        super().__init__("reversal", depth)
        self.cfg = cfg
        self._dir: dict[str, float] = {}
        self._stops: dict[str, float] = {}
        self._days_since = 10 ** 9

    def generate_signals(self, state: MarketState) -> list[Signal]:
        cfg = self.cfg
        if self._ingest_daily(state):
            self._days_since += 1
        if self._days_since >= cfg.rebalance_days:
            self._rebalance()
            self._days_since = 0

        out: list[Signal] = []
        for sym, direction in sorted(self._dir.items()):
            stop = self._stops.get(sym)
            if stop is None or sym not in state.bars:
                continue
            out.append(Signal(
                strategy=self.name, symbol=sym, direction=direction,
                stop_distance=stop, expected_edge_R=cfg.expected_edge_R, tag=sym,
            ))
        return out

    def _rebalance(self) -> None:
        cfg = self.cfg
        need = max(cfg.lookback_days + 1, cfg.atr_n + 2)
        stale_before = None
        if self._cur_date is not None:
            stale_before = (date.fromisoformat(self._cur_date)
                            - timedelta(days=10)).isoformat()
        ret: dict[str, float] = {}
        atr_d: dict[str, float] = {}
        for sym, dq in sorted(self._panel.items()):
            if len(dq) < need:
                continue
            if stale_before is not None and dq[-1][0] < stale_before:
                continue   # fell out of the live view: frozen prices, drop
            rows = list(dq)
            c = np.asarray([r[3] for r in rows], dtype=float)
            if not np.all(np.isfinite(c[-need:])) or (c[-need:] <= 0).any():
                continue
            r = c[-1] / c[-1 - cfg.lookback_days] - 1.0
            a = atr_from_rows(rows, cfg.atr_n)
            if math.isfinite(r) and math.isfinite(a) and a > 0:
                ret[sym], atr_d[sym] = r, a

        syms = sorted(ret)
        if len(syms) < cfg.min_universe:       # insufficient breadth -> no-op
            self._dir, self._stops = {}, {}
            return
        ranked = sorted(syms, key=lambda s: ret[s])
        k = min(cfg.top_k, len(ranked) // 2)
        new: dict[str, float] = {}
        for s in ranked[:k]:                   # losers -> LONG (reversal)
            new[s] = 1.0
        if cfg.market_neutral:
            for s in ranked[-k:]:              # winners -> SHORT
                new[s] = -1.0
        self._dir = new
        self._stops = {s: cfg.atr_mult * atr_d[s] for s in new}

    # ----------------------------------------------------------- persistence
    def state_dict(self) -> dict:
        d = super().state_dict()
        d.update({"dir": dict(self._dir), "stops": dict(self._stops),
                  "days_since": self._days_since})
        return d

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        self._dir = {k: float(v) for k, v in d.get("dir", {}).items()}
        self._stops = {k: float(v) for k, v in d.get("stops", {}).items()}
        self._days_since = d.get("days_since", 10 ** 9)
