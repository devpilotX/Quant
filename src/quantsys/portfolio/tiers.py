"""Capital-tier ladder: discrete gates (universe size, strategy count,
execution style) step at thresholds with hysteresis; continuous parameters
(ADV cap, cost multiple, rebalance band, leverage cap) interpolate in
log-equity between tier anchors, so nothing jumps when E(t) drifts.

Hysteresis: promotion is immediate at the threshold; demotion only when E
falls below (1 - hysteresis) * threshold — an account oscillating around a
boundary doesn't flap its whole configuration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quantsys.config.schema import TierConfig, TiersConfig
from quantsys.core.types import ExecutionStyle


@dataclass(frozen=True)
class TierState:
    name: str
    index: int
    max_instruments: int
    max_strategies: int
    execution_style: ExecutionStyle
    adv_cap_pct: float
    min_cost_multiple: float
    rebalance_band: float
    gross_leverage_cap: float


class TierLadder:
    def __init__(self, cfg: TiersConfig):
        self.cfg = cfg
        self.ladder: list[TierConfig] = cfg.ladder
        self._idx: int | None = None

    def resolve(self, equity: float) -> TierState:
        target = 0
        for i, t in enumerate(self.ladder):
            if equity >= t.min_equity:
                target = i
        if self._idx is None:
            self._idx = target
        elif target > self._idx:
            self._idx = target  # promote immediately
        else:
            while self._idx > 0 and equity < (1.0 - self.cfg.hysteresis) * self.ladder[self._idx].min_equity:
                self._idx -= 1  # demote through as many bands as E truly fell

        i = self._idx
        cur = self.ladder[i]
        nxt = self.ladder[i + 1] if i + 1 < len(self.ladder) else None

        def lerp(a: float, b: float) -> float:
            if nxt is None or equity <= cur.min_equity:
                return a
            t = (math.log(equity) - math.log(cur.min_equity)) / (
                math.log(nxt.min_equity) - math.log(cur.min_equity)
            )
            t = min(max(t, 0.0), 1.0)
            return a + t * (b - a)

        n = nxt or cur
        return TierState(
            name=cur.name,
            index=i,
            max_instruments=cur.max_instruments,
            max_strategies=cur.max_strategies,
            execution_style=cur.execution_style,
            adv_cap_pct=lerp(cur.adv_cap_pct, n.adv_cap_pct),
            min_cost_multiple=lerp(cur.min_cost_multiple, n.min_cost_multiple),
            rebalance_band=lerp(cur.rebalance_band, n.rebalance_band),
            gross_leverage_cap=lerp(cur.gross_leverage_cap, n.gross_leverage_cap),
        )

    def state_dict(self) -> dict:
        return {"idx": self._idx}

    def load_state(self, d: dict) -> None:
        self._idx = d.get("idx")
