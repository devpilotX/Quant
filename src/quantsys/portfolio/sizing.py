"""Position sizing: signals -> fractional target quantities -> (after vol
targeting and risk caps) lot-rounded integer targets with a cost gate.

Per-signal risk budget:
    risk_i = E * risk_frac_eff * f_strategy * conviction_share_i
    qty_parent = sign(direction) * risk_i / (stop_distance * point_value)
Non-parent legs derive from the parent notional via their notional_ratio, so
hedge ratios are exact by construction.

The cost gate (binds hard at T1/T2): a NEW trade group must clear
    expected_edge_R * risk_rupees >= tier.min_cost_multiple * round_trip_cost.
Existing positions whose signal persists are not gated (gating a held position
would force-pay the exit cost the gate is trying to avoid). Equity costs are
gated at delivery rates — worst case, capital-preservation bias.
"""

from __future__ import annotations

import math

from quantsys.config.schema import SizingConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import AuditEvent, InstrumentKind, Position, Signal, TargetPosition, Urgency
from quantsys.costs import CostModel
from quantsys.portfolio.book import Component, TargetBook
from quantsys.portfolio.tiers import TierState


class SizingEngine:
    def __init__(self, cfg: SizingConfig, cost_model: CostModel,
                 min_order_notional: float, min_order_frac: float = 0.0):
        self.cfg = cfg
        self.cost_model = cost_model
        self.min_order_notional = min_order_notional
        self.min_order_frac = min_order_frac

    def effective_min_notional(self, equity: float) -> float:
        """Dust floor scaled to the book: max(flat floor, frac * equity)."""
        return max(self.min_order_notional, self.min_order_frac * max(equity, 0.0))

    # ------------------------------------------------------------ raw build
    def build_raw(
        self,
        signals: list[Signal],
        kelly_f: dict[str, float],
        risk_amount_base: float,   # E * risk_frac_eff (throttled)
        state: MarketState,
        audits: list[AuditEvent],
    ) -> TargetBook:
        book = TargetBook()
        by_strategy: dict[str, list[Signal]] = {}
        for sig in signals:
            by_strategy.setdefault(sig.strategy, []).append(sig)

        for strat in sorted(by_strategy):
            f_s = kelly_f.get(strat, 0.0)
            if f_s <= 0 or risk_amount_base <= 0:
                continue
            sigs = sorted(by_strategy[strat], key=lambda s: (-abs(s.direction), s.group_id))
            sigs = sigs[: self.cfg.max_signals_per_strategy]
            total_conv = sum(abs(s.direction) for s in sigs)
            for sig in sigs:
                share = (
                    abs(sig.direction) / total_conv
                    if self.cfg.conviction_weighting and total_conv > 0
                    else 1.0 / len(sigs)
                )
                comps = self._size_group(sig, risk_amount_base * f_s * share, state, audits)
                if comps:
                    book.add_group(comps, sig)
        return book

    def _size_group(
        self, sig: Signal, risk_amt: float, state: MarketState, audits: list[AuditEvent]
    ) -> list[Component] | None:
        parent_inst = state.instruments.get(sig.symbol)
        p_parent = state.price(sig.symbol)
        if parent_inst is None or not (math.isfinite(p_parent) and p_parent > 0):
            audits.append(AuditEvent("sizing", "bad_price", sig.group_id, symbol=sig.symbol))
            return None
        stop_d = max(sig.stop_distance, self.cfg.atr_min_stop_ticks * parent_inst.tick_size)
        if not (math.isfinite(stop_d) and stop_d > 0):
            audits.append(AuditEvent("sizing", "bad_stop", sig.group_id, symbol=sig.symbol))
            return None

        sgn = 1.0 if sig.direction > 0 else -1.0
        qty_parent = sgn * risk_amt / (stop_d * parent_inst.point_value)
        parent_notional = abs(qty_parent) * p_parent * parent_inst.point_value

        comps: list[Component] = []
        for leg in sig.resolved_legs():
            inst = state.instruments.get(leg.symbol)
            p = state.price(leg.symbol)
            if inst is None or not (math.isfinite(p) and p > 0):
                audits.append(AuditEvent("sizing", "bad_leg", sig.group_id, symbol=leg.symbol))
                return None
            leg_qty = sgn * math.copysign(1.0, leg.notional_ratio) * (
                parent_notional * abs(leg.notional_ratio) / (p * inst.point_value)
            )
            comps.append(
                Component(
                    symbol=leg.symbol,
                    qty=leg_qty,
                    strategy=sig.strategy,
                    group_id=sig.group_id,
                    stop_distance=stop_d * p / p_parent,  # proportional backstop
                    ref_price=p,
                    is_parent=(leg.symbol == sig.symbol and leg.notional_ratio == 1.0),
                )
            )
        return comps

    # -------------------------------------------------------------- finalize
    def finalize(
        self,
        book: TargetBook,
        tier: TierState,
        state: MarketState,
        positions: dict[str, Position],
        sigma_daily: dict[str, float],
        audits: list[AuditEvent],
    ) -> list[TargetPosition]:
        out: list[TargetPosition] = []
        for gid, comps in sorted(book.group_components().items()):
            sig = book.group_meta.get(gid)
            rounded: list[tuple[Component, int]] = []
            ok = True
            for c in comps:
                inst = state.instruments[c.symbol]
                lot = max(inst.lot_size, 1)
                q = int(abs(c.qty) // lot) * lot * (1 if c.qty > 0 else -1)
                if q == 0 and self._promote_min_lot(c, comps, inst, state):
                    q = lot if c.qty > 0 else -lot
                    audits.append(AuditEvent("sizing", "min_lot_promotion", gid,
                                             symbol=c.symbol, before=c.qty, after=float(q)))
                if q == 0:
                    audits.append(AuditEvent("sizing", "rounds_to_zero", gid, symbol=c.symbol,
                                             before=c.qty, after=0.0))
                    ok = False
                    break
                rounded.append((c, q))
            if not ok:
                continue

            gross = sum(abs(q) * state.price(c.symbol) * state.instruments[c.symbol].point_value
                        for c, q in rounded)
            if gross < self.effective_min_notional(state.equity):
                audits.append(AuditEvent("sizing", "dust", gid, before=gross, after=0.0))
                continue

            if self.cfg.enforce_cost_gate and sig is not None and self._is_new_trade(rounded, positions):
                parent = next(((c, q) for c, q in rounded if c.is_parent), rounded[0])
                c_p, q_p = parent
                risk_rupees = abs(q_p) * c_p.stop_distance * state.instruments[c_p.symbol].point_value
                edge_rupees = sig.expected_edge_R * risk_rupees
                rt = 0.0
                for c, q in rounded:
                    inst = state.instruments[c.symbol]
                    rt += self.cost_model.round_trip(
                        inst, q, state.price(c.symbol),
                        delivery=(inst.kind == InstrumentKind.EQUITY),
                        sigma_daily=sigma_daily.get(c.symbol),
                    )
                if edge_rupees < tier.min_cost_multiple * rt:
                    audits.append(AuditEvent("sizing", "cost_gate",
                                             f"{gid}: edge Rs{edge_rupees:.0f} < {tier.min_cost_multiple:.1f}x cost Rs{rt:.0f}"))
                    continue

            for c, q in rounded:
                out.append(TargetPosition(
                    symbol=c.symbol, qty=q, strategy=c.strategy, group_id=gid,
                    stop_distance=c.stop_distance, ref_price=c.ref_price, urgency=Urgency.NORMAL,
                ))
        return out

    def _promote_min_lot(self, c: Component, comps: list[Component],
                         inst, state: MarketState) -> bool:
        """Index-futures unlock: one contract is the market's minimum ticket, so
        a lot-sized FUTURE whose risk budget rounds below one lot may take
        exactly ONE lot iff that lot's rupee risk stays within
        promotion_max_risk_frac of equity AND the budget already wanted at least
        promotion_min_lot_frac of a lot. Single-leg groups only — promoting one
        leg of a spread would corrupt the hedge ratio.

        The second test is what makes this a ROUNDING rule. Without it the risk
        cap alone let a target of 0.10 lots become a full lot — a 10x size
        manufacture, not a rounding — which is how 852 promotions turned
        BANKNIFTY/NIFTY futures into 71% of Study 2's gross loss."""
        if not self.cfg.min_lot_promotion or len(comps) != 1:
            return False
        if inst.kind != InstrumentKind.FUTURE or inst.lot_size <= 1:
            return False
        if abs(c.qty) < self.cfg.promotion_min_lot_frac * inst.lot_size:
            return False
        lot_risk = inst.lot_size * c.stop_distance * inst.point_value
        return (state.equity > 0
                and lot_risk <= self.cfg.promotion_max_risk_frac * state.equity)

    @staticmethod
    def _is_new_trade(rounded: list[tuple[Component, int]], positions: dict[str, Position]) -> bool:
        """A group is 'held' iff every leg already has a same-direction position."""
        for c, q in rounded:
            pos = positions.get(c.symbol)
            if pos is None or pos.qty == 0 or (pos.qty > 0) != (q > 0):
                return True
        return False
