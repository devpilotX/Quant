"""TargetBook: the mutable proposal that flows through the sizing/risk
pipeline. Components keep (strategy, group) attribution; every scaling
operation is GROUP-JOINT so multi-leg trades keep their hedge ratios under
any cap. All scalings are monotone-decreasing, so a single ordered pass of
risk rules cannot re-violate an earlier cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from quantsys.core.types import Instrument, Signal


@dataclass
class Component:
    symbol: str
    qty: float                  # signed; fractional until finalize() rounds
    strategy: str
    group_id: str
    stop_distance: float        # per-leg catastrophic backstop, price units
    ref_price: float
    is_parent: bool = True


@dataclass
class TargetBook:
    components: list[Component] = field(default_factory=list)
    group_meta: dict[str, Signal] = field(default_factory=dict)

    def add_group(self, comps: list[Component], signal: Signal) -> None:
        self.components.extend(comps)
        self.group_meta[comps[0].group_id] = signal

    @property
    def empty(self) -> bool:
        return not self.components

    def symbols(self) -> list[str]:
        return sorted({c.symbol for c in self.components})

    def net_qty(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in self.components:
            out[c.symbol] = out.get(c.symbol, 0.0) + c.qty
        return out

    def net_notional(self, prices: Mapping[str, float], instruments: Mapping[str, Instrument]) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym, q in self.net_qty().items():
            pv = instruments[sym].point_value if sym in instruments else 1.0
            out[sym] = q * prices.get(sym, 0.0) * pv
        return out

    def gross(self, prices, instruments) -> float:
        return sum(abs(v) for v in self.net_notional(prices, instruments).values())

    def net(self, prices, instruments) -> float:
        return sum(self.net_notional(prices, instruments).values())

    def groups_touching(self, symbol: str) -> list[str]:
        return sorted({c.group_id for c in self.components if c.symbol == symbol})

    def scale_group(self, group_id: str, factor: float) -> None:
        for c in self.components:
            if c.group_id == group_id:
                c.qty *= factor

    def scale_symbol(self, symbol: str, factor: float) -> None:
        """Scale every group touching `symbol` -> symbol net scales by exactly
        `factor`; collateral shrink of sibling legs is intentional (conservative)."""
        for gid in self.groups_touching(symbol):
            self.scale_group(gid, factor)

    def scale_all(self, factor: float) -> None:
        for c in self.components:
            c.qty *= factor

    def drop_group(self, group_id: str) -> None:
        self.components = [c for c in self.components if c.group_id != group_id]
        self.group_meta.pop(group_id, None)

    def group_components(self) -> dict[str, list[Component]]:
        out: dict[str, list[Component]] = {}
        for c in self.components:
            out.setdefault(c.group_id, []).append(c)
        return out
