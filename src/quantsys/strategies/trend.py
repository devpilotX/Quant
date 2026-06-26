"""Time-series momentum + breakout on a slow bar clock. The ensemble backbone.

Score = (1-w)*tanh(EMA-spread / ATR / scale) + w*breakout_state, with
entry/exit hysteresis so positions don't flap around the threshold. The
strategy emits a signal EVERY decision bar while it wants a position —
"stop emitting" IS the exit instruction (declarative targets, idempotent).
"""

from __future__ import annotations

import math

import numpy as np

from quantsys.config.schema import TrendConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import InstrumentKind, Signal
from quantsys.data.features import atr, donchian, ema
from quantsys.strategies.base import Strategy, register

_TRADEABLE = {InstrumentKind.EQUITY, InstrumentKind.FUTURE}


@register("trend")
class TrendStrategy(Strategy):
    def __init__(self, cfg: TrendConfig):
        super().__init__("trend")
        self.cfg = cfg
        self._dir: dict[str, float] = {}  # symbol -> held conviction (signed)

    def warmup_bars(self) -> int:
        cfg = self.cfg
        need = max(cfg.ema_slow, cfg.donchian, cfg.atr_n) + 10
        return cfg.timeframe_bars * need

    def generate_signals(self, state: MarketState) -> list[Signal]:
        cfg = self.cfg
        out: list[Signal] = []
        need = max(cfg.ema_slow, cfg.donchian, cfg.atr_n) + 2

        for sym in sorted(state.bars):
            inst = state.instruments.get(sym)
            if inst is None or inst.kind not in _TRADEABLE:
                continue
            rs = state.bars[sym].resampled(cfg.timeframe_bars)
            if not rs or len(rs["close"]) < need:
                self._dir.pop(sym, None)
                continue
            c, h, lo = rs["close"], rs["high"], rs["low"]
            a = atr(h, lo, c, cfg.atr_n)
            if not math.isfinite(a) or a <= 0:
                self._dir.pop(sym, None)
                continue

            mom = (ema(c, cfg.ema_fast)[-1] - ema(c, cfg.ema_slow)[-1]) / a
            hh, ll = donchian(h, lo, cfg.donchian)
            brk = 1.0 if c[-1] > hh else (-1.0 if c[-1] < ll else 0.0)
            w = cfg.breakout_weight
            score = (1.0 - w) * math.tanh(mom / cfg.momentum_scale) + w * brk

            new_dir = self._hysteresis(self._dir.get(sym, 0.0), score)
            if new_dir == 0.0:
                self._dir.pop(sym, None)
                continue
            self._dir[sym] = new_dir
            out.append(
                Signal(
                    strategy=self.name,
                    symbol=sym,
                    direction=new_dir,
                    stop_distance=cfg.atr_mult * a,
                    expected_edge_R=cfg.expected_edge_R,
                    tag=sym,
                )
            )
        return out

    def _hysteresis(self, prev: float, score: float) -> float:
        cfg = self.cfg
        mag, sgn = abs(score), math.copysign(1.0, score) if score else 0.0
        if prev == 0.0:
            return float(np.clip(score, -1.0, 1.0)) if mag >= cfg.entry_threshold else 0.0
        same_side = sgn == math.copysign(1.0, prev)
        if same_side and mag >= cfg.exit_threshold:
            # hold; conviction floors at 0.3 so the book doesn't dribble out
            return math.copysign(min(max(mag, 0.3), 1.0), prev)
        if not same_side and mag >= cfg.entry_threshold:
            return float(np.clip(score, -1.0, 1.0))  # clean reversal
        return 0.0

    def state_dict(self) -> dict:
        return {"dir": dict(self._dir)}

    def load_state(self, d: dict) -> None:
        self._dir = {k: float(v) for k, v in d.get("dir", {}).items()}
