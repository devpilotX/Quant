"""Target-vs-position order diffing with anti-churn bands.

Rules:
- Full exits (target 0) and risk-driven orders always go out.
- Rebalance deltas smaller than max(1 lot, band * |target|) are skipped —
  at small capital the band is wide (cost drag), at large capital narrow.
- Sub-min-notional dribbles are skipped unless exiting.
- KILL flattens at market; stop hits are RISK_REDUCING (cross the spread);
  everything else uses the tier's execution style.
"""

from __future__ import annotations

from typing import Mapping

from quantsys.core.types import (
    ExecutionStyle,
    Instrument,
    OrderIntent,
    Position,
    TargetPosition,
    Urgency,
)
from quantsys.portfolio.tiers import TierState


def diff_orders(
    targets: list[TargetPosition],
    positions: Mapping[str, Position],
    instruments: Mapping[str, Instrument],
    prices: Mapping[str, float],
    tier: TierState,
    min_order_notional: float,
    risk_reducing_symbols: set[str] = frozenset(),
    kill: bool = False,
) -> list[OrderIntent]:
    net: dict[str, int] = {}
    strat: dict[str, dict[str, int]] = {}
    for t in targets:
        net[t.symbol] = net.get(t.symbol, 0) + t.qty
        strat.setdefault(t.symbol, {})[t.strategy] = (
            strat.get(t.symbol, {}).get(t.strategy, 0) + abs(t.qty)
        )

    out: list[OrderIntent] = []
    for sym in sorted(set(net) | {s for s, p in positions.items() if p.qty != 0}):
        cur = positions[sym].qty if sym in positions else 0
        tgt = net.get(sym, 0)

        if kill:  # flatten unconditionally — before any delta short-circuit
            if cur != 0:
                out.append(OrderIntent(sym, -cur, ExecutionStyle.MARKET_SINGLE,
                                       Urgency.KILL, reason="kill_switch"))
            continue

        delta = tgt - cur
        if delta == 0:
            continue
        inst = instruments.get(sym)
        lot = max(inst.lot_size, 1) if inst else 1
        price = prices.get(sym, 0.0)

        urgency = Urgency.RISK_REDUCING if sym in risk_reducing_symbols else Urgency.NORMAL
        if tgt != 0 and urgency is Urgency.NORMAL:
            if abs(delta) < max(lot, tier.rebalance_band * abs(tgt)):
                continue  # anti-churn band
            if price > 0 and abs(delta) * price * (inst.point_value if inst else 1.0) < min_order_notional:
                continue

        dominant = max(strat.get(sym, {"": 0}), key=lambda k: strat.get(sym, {}).get(k, 0))
        style = ExecutionStyle.MARKET_SINGLE if urgency is not Urgency.NORMAL else tier.execution_style
        out.append(OrderIntent(sym, delta, style, urgency, strategy=dominant,
                               reason="rebalance" if cur and tgt else ("exit" if tgt == 0 else "entry")))
    return out
