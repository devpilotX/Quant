"""Pillar 2 — broad-universe cross-sectional equity factors (momentum + low-vol).

A DAILY, breadth-dependent strategy expressed in the standard Strategy interface:
on a monthly cadence it ranks the tradeable universe by a composite of 12-1
momentum and low-volatility, then holds an equal-weight basket of the best names
(and, if market_neutral, shorts the worst) until the next rebalance. Declarative
targets: a signal is emitted every bar while a name is wanted; ceasing to emit IS
the exit (identical contract to trend/meanrev).

SAFE BY DESIGN in the live engine: it needs >= ``min_universe`` names with enough
history, so on the tier-capped intraday book (3-20 names) it emits nothing — a
pure no-op. It is also DISABLED by default and stays off until the research
harness (`quantsys.research.run_pillar2`) demonstrates a gate-clearing OOS edge on
a broad daily feed. Corporate-action adjustment is assumed handled by the feed
(the research harness does it explicitly; see docs/PILLAR2_FACTOR_RESEARCH.md).
"""

from __future__ import annotations

import math

import numpy as np

from quantsys.config.schema import FactorConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import InstrumentKind, Signal
from quantsys.data.features import atr
from quantsys.strategies.base import Strategy, register

_TRADEABLE = {InstrumentKind.EQUITY, InstrumentKind.FUTURE}


@register("factor")
class FactorStrategy(Strategy):
    def __init__(self, cfg: FactorConfig):
        super().__init__("factor")
        self.cfg = cfg
        self._dir: dict[str, float] = {}     # symbol -> held direction (+1 long / -1 short)
        self._bars_since = 10**9

    def warmup_bars(self) -> int:
        cfg = self.cfg
        return cfg.timeframe_bars * (max(cfg.lookback_bars, cfg.vol_lookback) + cfg.skip_bars + 5)

    def generate_signals(self, state: MarketState) -> list[Signal]:
        cfg = self.cfg
        self._bars_since += 1
        if self._bars_since >= cfg.rebalance_bars:
            self._rebalance(state)
            self._bars_since = 0

        out: list[Signal] = []
        for sym, d in sorted(self._dir.items()):
            rs = state.bars[sym].resampled(cfg.timeframe_bars) if sym in state.bars else None
            if not rs or len(rs["close"]) < cfg.atr_n + 2:
                continue
            a = atr(rs["high"], rs["low"], rs["close"], cfg.atr_n)
            if not (math.isfinite(a) and a > 0):
                continue
            out.append(Signal(
                strategy=self.name, symbol=sym, direction=d,
                stop_distance=cfg.atr_mult * a, expected_edge_R=cfg.expected_edge_R, tag=sym,
            ))
        return out

    # ------------------------------------------------------------- rebalance
    def _rebalance(self, state: MarketState) -> None:
        cfg = self.cfg
        mom: dict[str, float] = {}
        lvol: dict[str, float] = {}
        for sym in sorted(state.bars):
            inst = state.instruments.get(sym)
            if inst is None or inst.kind not in _TRADEABLE:
                continue
            rs = state.bars[sym].resampled(cfg.timeframe_bars)
            if not rs or len(rs["close"]) < cfg.lookback_bars + cfg.skip_bars + 1:
                continue
            c = np.asarray(rs["close"], dtype=float)
            if not np.all(np.isfinite(c[-(cfg.lookback_bars + cfg.skip_bars + 1):])) or (c <= 0).any():
                continue
            m = c[-1 - cfg.skip_bars] / c[-1 - cfg.lookback_bars] - 1.0
            r = np.diff(np.log(c[-(cfg.vol_lookback + 1):]))
            v = float(np.std(r)) if r.size > 2 else float("nan")
            if math.isfinite(m) and math.isfinite(v) and v > 0:
                mom[sym], lvol[sym] = m, -v   # -v: low vol = attractive

        syms = [s for s in mom if s in lvol]
        if len(syms) < cfg.min_universe:        # insufficient breadth -> full no-op
            self._dir = {}
            return

        score = _zsum(mom, syms, _zsum(lvol, syms, None))
        ranked = sorted(syms, key=lambda s: score[s])
        k = min(cfg.top_k, len(ranked) // 2)
        new: dict[str, float] = {}
        for s in ranked[-k:]:
            new[s] = 1.0
        if cfg.market_neutral:
            for s in ranked[:k]:
                new[s] = -1.0
        self._dir = new

    def state_dict(self) -> dict:
        return {"dir": dict(self._dir), "bars_since": self._bars_since}

    def load_state(self, d: dict) -> None:
        self._dir = {k: float(v) for k, v in d.get("dir", {}).items()}
        self._bars_since = d.get("bars_since", 10**9)


def _z(vals: dict[str, float], syms: list[str]) -> dict[str, float]:
    arr = np.array([vals[s] for s in syms], dtype=float)
    mu, sd = arr.mean(), arr.std()
    if sd == 0:
        return {s: 0.0 for s in syms}
    return {s: float((vals[s] - mu) / sd) for s in syms}


def _zsum(vals: dict[str, float], syms: list[str], acc: dict[str, float] | None) -> dict[str, float]:
    z = _z(vals, syms)
    if acc is None:
        return z
    return {s: acc.get(s, 0.0) + z[s] for s in syms}
