"""PaperBroker: executes the engine's OrderIntents against the live/replayed
price with the SAME CostModel the engine uses. Honest economics:

- fill price = current bar close (the engine's ref price),
- every fee line (brokerage/STT/exchange/SEBI/stamp/GST/slippage/impact)
  computed by quantsys.costs.CostModel and stored per-fill,
- positions, realized P&L (average-price method), cash and equity tracked
  exactly; equity feeds back into the next MarketState so sizing reacts.

This is the paper-mode execution venue, not a mock: numbers shown in the
dashboard are what the cost model says trading would have cost.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from quantsys.core.types import (
    Decision,
    Instrument,
    InstrumentKind,
    OrderIntent,
    Position,
)
from quantsys.costs import CostModel

from qsdash.audit import notify_alert
from qsdash.bus import PgSyncPublisher, SyncPublisher
from qsdash.db import SessionLocal, now_ist
from qsdash.models import (
    FillRow,
    OrderRow,
    PnlAttribution,
    PositionRow,
)

log = logging.getLogger(__name__)


class PaperBroker:
    def __init__(self, mode: str, starting_cash: float,
                 instruments: dict[str, Instrument], cost_model: CostModel,
                 publisher: SyncPublisher):
        self.mode = mode  # 'paper' (kept explicit so rows are stamped correctly)
        self.cash = starting_cash
        self.instruments = instruments
        self.cost_model = cost_model
        self.publisher = publisher
        self.positions: dict[str, Position] = {}
        self.realized_total = 0.0
        self._open_rows: dict[str, int] = {}  # symbol -> positions.id
        # trade alerts are collected during fills and emitted AFTER the fill
        # transaction commits, so an alert only ever describes a persisted trade
        self._pending_trade_alerts: list[dict] = []

    # ------------------------------------------------------------ helpers
    def load_open_state(self) -> None:
        """Resume open paper positions from the DB after a restart."""
        sess = SessionLocal()
        try:
            rows = (sess.query(PositionRow)
                    .filter(PositionRow.mode == self.mode,
                            PositionRow.status == "open").all())
            for r in rows:
                self.positions[r.symbol] = Position(r.symbol, r.qty, r.avg_price)
                self._open_rows[r.symbol] = r.id
        finally:
            sess.close()

    def mtm(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            inst = self.instruments.get(sym)
            if px is None or inst is None:
                continue
            total += pos.qty * px * inst.point_value
        return total

    def unrealized(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            inst = self.instruments.get(sym)
            if px is None or inst is None:
                continue
            total += pos.qty * (px - pos.avg_price) * inst.point_value
        return total

    def exposures(self, prices: dict[str, float]) -> tuple[float, float]:
        gross = net = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            inst = self.instruments.get(sym)
            if px is None or inst is None:
                continue
            notional = pos.qty * px * inst.point_value
            gross += abs(notional)
            net += notional
        return gross, net

    @property
    def equity_marked(self) -> float:
        raise NotImplementedError("use equity(prices)")

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.mtm(prices)

    # ------------------------------------------------------------ execution
    def execute(self, decision: Decision, decision_id: int,
                prices: dict[str, float], ts: datetime) -> None:
        """Fill every order intent immediately at the bar close."""
        if not decision.orders:
            return
        sess = SessionLocal()
        if isinstance(self.publisher, PgSyncPublisher):
            self.publisher.bind(sess)
        try:
            for intent in decision.orders:
                self._fill_one(sess, intent, decision, decision_id, prices, ts)
            sess.commit()
        except Exception:
            sess.rollback()
            self._pending_trade_alerts.clear()  # drop alerts for rolled-back fills
            raise
        finally:
            if isinstance(self.publisher, PgSyncPublisher):
                self.publisher.bind(None)
            sess.close()
        self._emit_trade_alerts()

    def _fill_one(self, sess, intent: OrderIntent, decision: Decision,
                  decision_id: int, prices: dict[str, float], ts: datetime) -> None:
        sym = intent.symbol
        inst = self.instruments.get(sym)
        px = prices.get(sym)
        if inst is None or px is None or not (px > 0):
            log.warning("paper: cannot fill %s — no price/instrument", sym)
            return
        qty = intent.qty_delta
        if qty == 0:
            return
        is_buy = qty > 0
        cb = self.cost_model.order_cost(
            inst, abs(qty), px, is_buy,
            delivery=(inst.kind == InstrumentKind.EQUITY),
        )
        fees = {
            "brokerage": cb.brokerage, "stt": cb.stt, "exchange_txn": cb.exchange_txn,
            "sebi": cb.sebi, "stamp": cb.stamp, "gst": cb.gst,
            "slippage": cb.slippage, "impact": cb.impact,
        }
        coid = f"P-{ts.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

        order = OrderRow(
            client_order_id=coid, decision_id=decision_id, ts=ts, mode=self.mode,
            strategy=intent.strategy, symbol=sym,
            side="BUY" if is_buy else "SELL", qty=abs(qty), filled_qty=abs(qty),
            style=intent.style.value, urgency=intent.urgency.value,
            ref_price=px, status="FILLED", reason=intent.reason,
            status_history=[{"ts": ts.isoformat(), "status": "FILLED",
                             "via": "paper"}],
        )
        sess.add(order)
        sess.flush()
        sess.add(FillRow(
            order_id=order.id, client_order_id=coid, ts=ts, mode=self.mode,
            symbol=sym, strategy=intent.strategy, qty=qty, price=px,
            fees_total=cb.total, fees=fees, slippage=cb.slippage,
        ))

        realized, strat_used, closed = self._apply_to_position(
            sess, intent, decision, decision_id, qty, px, cb.total, inst, ts
        )
        self._attribute_pnl(sess, ts, strat_used, sym, decision,
                            realized, cb.total, closed)
        # cash: trade flow + fees
        self.cash -= qty * px * inst.point_value
        self.cash -= cb.total
        self.realized_total += realized

        self.publisher.publish("orders", {
            "id": order.id, "client_order_id": coid, "ts": ts.isoformat(),
            "mode": self.mode, "symbol": sym, "side": order.side,
            "qty": order.qty, "status": "FILLED", "strategy": intent.strategy,
            "price": px, "fees_total": cb.total, "urgency": order.urgency,
        })
        self.publisher.publish("fills", {
            "ts": ts.isoformat(), "symbol": sym, "qty": qty, "price": px,
            "fees_total": cb.total, "strategy": intent.strategy,
        })

    # ----------------------------------------------------------- positions
    def _entry_rationale(self, decision: Decision, sym: str, strategy: str) -> dict:
        sig = next((s for s in decision.signals
                    if s.symbol == sym or any(l.symbol == sym for l in s.resolved_legs())),
                   None)
        sizing_audit = [
            {"stage": a.stage, "rule": a.rule, "detail": a.detail,
             "before": a.before, "after": a.after}
            for a in decision.audit
            if a.symbol in (None, sym)
        ]
        return {
            "decision_id_note": "see entry.decision for the full pass",
            "strategy": strategy,
            "signal": None if sig is None else {
                "direction": sig.direction, "stop_distance": sig.stop_distance,
                "expected_edge_R": sig.expected_edge_R, "tag": sig.tag,
                "horizon_bars": sig.horizon_bars,
            },
            "regime": {"label": decision.regime.label,
                       "probs": dict(decision.regime.probs),
                       "risk_scaler": decision.regime.risk_scaler},
            "sizing": {"risk_frac_eff": decision.risk_frac_eff,
                       "kelly": dict(decision.kelly),
                       "vol_scaler": decision.vol_scaler,
                       "tier": decision.tier_name},
            "audit": sizing_audit[:80],
        }

    def _exit_rationale(self, decision: Decision, intent: OrderIntent) -> dict:
        return {
            "urgency": intent.urgency.value,
            "reason": intent.reason or (
                "kill switch" if decision.kill_reason else "target change / rebalance"
            ),
            "kill_reason": decision.kill_reason,
            "regime": {"label": decision.regime.label,
                       "probs": dict(decision.regime.probs)},
        }

    def _apply_to_position(self, sess, intent: OrderIntent, decision: Decision,
                           decision_id: int, qty: int, px: float, fees: float,
                           inst: Instrument, ts: datetime) -> tuple[float, str, bool]:
        """Average-price bookkeeping; one open PositionRow per symbol.
        Returns (realized_pnl, attributed_strategy, trade_closed)."""
        pos = self.positions.get(intent.symbol)
        realized = 0.0
        closed = False
        row: PositionRow | None = None
        row_id = self._open_rows.get(intent.symbol)
        if row_id is not None:
            row = sess.get(PositionRow, row_id)

        if pos is None or pos.qty == 0:
            # opening a brand-new position
            self.positions[intent.symbol] = Position(intent.symbol, qty, px)
            row = PositionRow(
                mode=self.mode, symbol=intent.symbol, strategy=intent.strategy,
                qty=qty, avg_price=px, status="open", opened_at=ts,
                fees_paid=fees, entry_decision_id=decision_id,
                entry_rationale=self._entry_rationale(decision, intent.symbol,
                                                      intent.strategy),
            )
            sess.add(row)
            sess.flush()
            self._open_rows[intent.symbol] = row.id
            self._publish_position(row, px)
            self._queue_trade_alert("open", intent.symbol, intent.strategy,
                                    qty, px)
            return 0.0, intent.strategy, False

        # exits often carry no strategy tag — attribute to the position's
        strat = (row.strategy if row is not None and row.strategy
                 else intent.strategy)

        same_side = (pos.qty > 0) == (qty > 0)
        if same_side:
            new_qty = pos.qty + qty
            pos.avg_price = (pos.avg_price * abs(pos.qty) + px * abs(qty)) / abs(new_qty)
            pos.qty = new_qty
            if row is not None:
                row.qty = new_qty
                row.avg_price = pos.avg_price
                row.fees_paid += fees
        else:
            was_long = pos.qty > 0
            closing = min(abs(qty), abs(pos.qty))
            realized = closing * (px - pos.avg_price) * inst.point_value * (
                1 if was_long else -1
            )
            remaining = pos.qty + qty
            if row is not None:
                row.realized_pnl += realized
                row.fees_paid += fees
            if remaining == 0:
                closed = True
                pos.qty = 0
                if row is not None:
                    row.qty = 0
                    row.status = "closed"
                    row.closed_at = ts
                    row.exit_decision_id = decision_id
                    row.exit_rationale = self._exit_rationale(decision, intent)
                self.positions.pop(intent.symbol, None)
                self._open_rows.pop(intent.symbol, None)
                self._queue_trade_alert("close", intent.symbol, strat, qty, px,
                                        realized=realized)
            elif (remaining > 0) == was_long:
                pos.qty = remaining  # partial close, same side remains
                if row is not None:
                    row.qty = remaining
            else:
                # flipped through zero: close the old row, open a new one
                closed = True
                if row is not None:
                    row.qty = 0
                    row.status = "closed"
                    row.closed_at = ts
                    row.exit_decision_id = decision_id
                    row.exit_rationale = self._exit_rationale(decision, intent)
                self._queue_trade_alert("close", intent.symbol, strat, qty, px,
                                        realized=realized)
                self.positions[intent.symbol] = Position(intent.symbol, remaining, px)
                new_row = PositionRow(
                    mode=self.mode, symbol=intent.symbol, strategy=intent.strategy,
                    qty=remaining, avg_price=px, status="open", opened_at=ts,
                    entry_decision_id=decision_id,
                    entry_rationale=self._entry_rationale(decision, intent.symbol,
                                                          intent.strategy),
                )
                sess.add(new_row)
                sess.flush()
                self._open_rows[intent.symbol] = new_row.id
                row = new_row
                self._queue_trade_alert("open", intent.symbol, intent.strategy,
                                        remaining, px)
        if row is not None:
            self._publish_position(row, px)
        return realized, strat, closed

    def _attribute_pnl(self, sess, ts: datetime, strategy: str, symbol: str,
                       decision: Decision, realized: float, fees: float,
                       closed: bool) -> None:
        date = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        bucket = (
            sess.query(PnlAttribution)
            .filter(PnlAttribution.date == date,
                    PnlAttribution.mode == self.mode,
                    PnlAttribution.strategy == strategy,
                    PnlAttribution.symbol == symbol,
                    PnlAttribution.regime == decision.regime.label)
            .first()
        )
        if bucket is None:
            bucket = PnlAttribution(
                date=date, mode=self.mode, strategy=strategy,
                symbol=symbol, regime=decision.regime.label,
                gross_pnl=0.0, fees=0.0, net_pnl=0.0, n_trades=0,
            )
            sess.add(bucket)
        bucket.gross_pnl += realized
        bucket.fees += fees
        bucket.net_pnl = bucket.gross_pnl - bucket.fees
        if closed:
            bucket.n_trades += 1

    def _publish_position(self, row: PositionRow, px: float) -> None:
        self.publisher.publish("positions", {
            "id": row.id, "mode": self.mode, "symbol": row.symbol,
            "strategy": row.strategy, "qty": row.qty,
            "avg_price": row.avg_price, "last_price": px,
            "status": row.status, "realized_pnl": row.realized_pnl,
        })

    # --------------------------------------------------------------- alerts
    def _queue_trade_alert(self, action: str, symbol: str, strategy: str,
                           qty: int, px: float, realized: float | None = None) -> None:
        if action == "open":
            side = "LONG" if qty > 0 else "SHORT"
            self._pending_trade_alerts.append(dict(
                severity="info", kind="trade_open",
                title=f"Opened {side} {abs(qty)} {symbol} @ {px:.2f}",
                body=f"{self.mode} · strategy={strategy}"))
        else:  # close
            self._pending_trade_alerts.append(dict(
                severity="info", kind="trade_close",
                title=f"Closed {symbol}: realized {(realized or 0.0):+,.0f}",
                body=f"{self.mode} · strategy={strategy} · exit @ {px:.2f}"))

    def _emit_trade_alerts(self) -> None:
        """Emit queued trade alerts AFTER the fill transaction committed."""
        pending, self._pending_trade_alerts = self._pending_trade_alerts, []
        for a in pending:
            notify_alert(self.publisher, **a)

    # ------------------------------------------------------------- control
    def flatten_all(self, prices: dict[str, float], ts: datetime,
                    reason: str = "operator flatten") -> None:
        """Close everything at market (paper: at current close)."""
        from quantsys.core.types import ExecutionStyle, Urgency

        sess = SessionLocal()
        if isinstance(self.publisher, PgSyncPublisher):
            self.publisher.bind(sess)
        try:
            for sym, pos in list(self.positions.items()):
                if pos.qty == 0:
                    continue
                intent = OrderIntent(
                    symbol=sym, qty_delta=-pos.qty,
                    style=ExecutionStyle.MARKET_SINGLE, urgency=Urgency.KILL,
                    strategy="operator", reason=reason,
                )
                fake = _FlattenDecision(ts)
                self._fill_one(sess, intent, fake, None, prices, ts)
            sess.commit()
        except Exception:
            sess.rollback()
            self._pending_trade_alerts.clear()
            raise
        finally:
            if isinstance(self.publisher, PgSyncPublisher):
                self.publisher.bind(None)
            sess.close()
        self._emit_trade_alerts()


class _FlattenDecision:
    """Minimal Decision stand-in for operator-initiated flattens (no engine
    pass involved — rationale says exactly that)."""

    def __init__(self, ts):
        from quantsys.core.types import RegimeState

        self.ts = ts
        self.signals = ()
        self.audit = ()
        self.kelly = {}
        self.vol_scaler = 1.0
        self.risk_frac_eff = 0.0
        self.tier_name = "-"
        self.kill_reason = "operator_flatten"
        self.regime = RegimeState("operator", {}, 1.0, {}, "none")
