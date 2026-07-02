"""Pillar 4 — down-shock underreaction sleeve (event-driven, daily panel).

The FROZEN research rule (docs/PILLAR4_EVENT_DRIVEN.md §8a + gate table; the
zero-risk tracker has logged it forward since 2026-06-25): after a >= z_threshold
sigma one-day DOWN move on >= volume_ratio_min x average volume, the stock keeps
drifting down — enter SHORT at the next session, hold `hold_days` sessions,
daily-ATR stop. Events are de-clustered per name; at most `max_concurrent`
concurrent shorts (most-negative z first).

Historical verdict was DEAD on the deflated-Sharpe gate (0.46 < 0.95) despite
4/5 passes and hold-out Sharpe 1.75 — this sleeve exists to collect the
forward paper evidence the closeout sanctioned, not because an edge is proven.
Deviation from the research construction, disclosed: no per-trade index-future
beta hedge (one hedge lot exceeds the explore-floor group budget and lot
rounding would drop every group) — the portfolio's net-exposure caps bound the
residual beta instead.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from quantsys.config.schema import DownShockConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import Signal
from quantsys.strategies._dailypanel import DailyPanelStrategy, atr_from_rows
from quantsys.strategies.base import register


@register("downshock")
class DownShockStrategy(DailyPanelStrategy):
    def __init__(self, cfg: DownShockConfig):
        depth = max(cfg.vol_window, cfg.volume_window, cfg.min_history_days,
                    cfg.atr_n + 2) + cfg.decluster_days + 10
        super().__init__("downshock", depth)
        self.cfg = cfg
        self._active: dict[str, int] = {}       # sym -> remaining hold sessions
        self._stops: dict[str, float] = {}      # sym -> stop distance
        self._last_event: dict[str, str] = {}   # sym -> iso date (de-cluster)

    # ------------------------------------------------------------- signals
    def generate_signals(self, state: MarketState) -> list[Signal]:
        if self._ingest_daily(state):
            self._on_session_roll()

        out: list[Signal] = []
        for sym in sorted(self._active):
            stop = self._stops.get(sym)
            if stop is None or sym not in state.bars:
                continue
            out.append(Signal(
                strategy=self.name, symbol=sym, direction=-1.0,
                stop_distance=stop, expected_edge_R=self.cfg.expected_edge_R,
                tag=sym,
            ))
        return out

    def _on_session_roll(self) -> None:
        """A session date rolled: age the held book, then scan the freshly
        finalized day for new down-shock events."""
        cfg = self.cfg
        for sym in list(self._active):
            self._active[sym] -= 1
            if self._active[sym] <= 0:
                self._active.pop(sym, None)
                self._stops.pop(sym, None)

        candidates: list[tuple[float, str, float]] = []   # (z, sym, stop)
        for sym, dq in sorted(self._panel.items()):
            need = max(cfg.vol_window + 2, cfg.volume_window + 2,
                       cfg.min_history_days, cfg.atr_n + 2)
            if len(dq) < need or sym in self._active:
                continue
            rows = list(dq)
            c = np.asarray([r[3] for r in rows], dtype=float)
            v = np.asarray([r[4] for r in rows], dtype=float)
            if not np.all(np.isfinite(c[-need:])) or (c[-need:] <= 0).any():
                continue
            rets = np.diff(c) / c[:-1]
            r_t = rets[-1]
            sigma = float(np.std(rets[-(cfg.vol_window + 1):-1]))   # lagged
            if not (math.isfinite(sigma) and sigma > 0):
                continue
            z = r_t / sigma
            vol_hist = v[-(cfg.volume_window + 1):-1]
            vol_mean = float(np.mean(vol_hist)) if vol_hist.size else 0.0
            if vol_mean <= 0 or v[-1] <= 0:
                continue   # no volume data (e.g. index rows) -> no event
            vol_ratio = float(v[-1]) / vol_mean
            if z > -cfg.z_threshold or vol_ratio < cfg.volume_ratio_min:
                continue
            last = self._last_event.get(sym)
            if last is not None:
                gap = (date.fromisoformat(rows[-1][0])
                       - date.fromisoformat(last))
                if gap < timedelta(days=cfg.decluster_days):
                    continue
            a = atr_from_rows(rows, cfg.atr_n)
            if not (math.isfinite(a) and a > 0):
                continue
            candidates.append((z, sym, cfg.atr_mult * a))

        candidates.sort()   # most-negative z first
        for z, sym, stop in candidates:
            if len(self._active) >= cfg.max_concurrent:
                break
            self._active[sym] = cfg.hold_days
            self._stops[sym] = stop
            self._last_event[sym] = self._cur_date or ""

    # ----------------------------------------------------------- persistence
    def state_dict(self) -> dict:
        d = super().state_dict()
        d.update({
            "active": dict(self._active),
            "stops": dict(self._stops),
            "last_event": dict(self._last_event),
        })
        return d

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        self._active = {k: int(v) for k, v in d.get("active", {}).items()}
        self._stops = {k: float(v) for k, v in d.get("stops", {}).items()}
        self._last_event = {k: str(v) for k, v in d.get("last_event", {}).items()}
