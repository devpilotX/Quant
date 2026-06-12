"""LiveExecutionBroker — presents the same surface the bridge Runner calls
(execute/flatten_all/positions/equity/...) but routes orders through the real
Angel One adapter + OMS, persists fills/orders/positions to Postgres, and
reconciles against the broker every cycle (freeze on mismatch).

It deliberately mirrors PaperBroker's method names so the Runner is venue-
agnostic; the ONLY behavioural difference is that fills come from the broker
(via postback/order-status), not synthesised at the bar close.
"""

from __future__ import annotations

import logging
from datetime import datetime

from quantsys.core.types import Decision, Instrument, OrderIntent
from quantsys.execution.broker import Broker, BrokerError, OrderStatus
from quantsys.execution.oms import OMS
from quantsys.execution.reconcile import reconcile_positions

from qsdash.bus import PgSyncPublisher, SyncPublisher
from qsdash.db import SessionLocal, now_ist
from qsdash.models import MODE_LIVE, OrderRow, PositionRow

log = logging.getLogger("qsdash.livebroker")


class LiveExecutionBroker:
    def __init__(self, broker: Broker, instruments: dict[str, Instrument],
                 publisher: SyncPublisher):
        self.mode = MODE_LIVE
        self.broker = broker
        self.instruments = instruments
        self.publisher = publisher
        self.oms = OMS(broker)
        self._last_recon_ok = True
        self._last_mismatch: list[str] = []

    # --------------------------------------------------------- marking
    def positions_map(self) -> dict[str, int]:
        try:
            return {p.symbol: p.qty for p in self.broker.positions() if p.qty != 0}
        except BrokerError as e:
            log.error("positions read failed: %s", e)
            return {}

    @property
    def positions(self) -> dict:
        # adapter to the Runner's expectation (symbol -> object with .qty)
        from quantsys.core.types import Position
        return {s: Position(s, q, 0.0) for s, q in self.positions_map().items()}

    def equity(self, prices: dict[str, float]) -> float:
        try:
            return self.broker.funds() + self._mtm(prices)
        except BrokerError as e:
            log.error("funds read failed: %s — using MTM only", e)
            return self._mtm(prices)

    def _mtm(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, q in self.positions_map().items():
            px = prices.get(sym)
            inst = self.instruments.get(sym)
            if px is not None and inst is not None:
                total += q * px * inst.point_value
        return total

    def mtm(self, prices: dict[str, float]) -> float:
        return self._mtm(prices)

    def unrealized(self, prices: dict[str, float]) -> float:
        # broker reports avg price; approximate with reported positions
        total = 0.0
        try:
            for p in self.broker.positions():
                px = prices.get(p.symbol)
                inst = self.instruments.get(p.symbol)
                if px is not None and inst is not None and p.qty:
                    total += p.qty * (px - p.avg_price) * inst.point_value
        except BrokerError:
            pass
        return total

    def exposures(self, prices: dict[str, float]) -> tuple[float, float]:
        gross = net = 0.0
        for sym, q in self.positions_map().items():
            px = prices.get(sym)
            inst = self.instruments.get(sym)
            if px is None or inst is None:
                continue
            notional = q * px * inst.point_value
            gross += abs(notional)
            net += notional
        return gross, net

    @property
    def cash(self) -> float:
        try:
            return self.broker.funds()
        except BrokerError:
            return 0.0

    @property
    def realized_total(self) -> float:
        return 0.0  # realized P&L is derived from fills in the DB for live

    def load_open_state(self) -> None:
        pass  # broker IS the state; nothing to resume locally

    # ------------------------------------------------------- reconciliation
    def reconcile(self, internal: dict[str, int]) -> tuple[bool, list[str]]:
        r = reconcile_positions(self.broker, internal)
        self._last_recon_ok = r.ok
        self._last_mismatch = r.mismatches
        return r.ok, r.mismatches

    # ------------------------------------------------------------ execution
    def execute(self, decision: Decision, decision_id: int,
                prices: dict[str, float], ts: datetime) -> None:
        """Route the decision's intents through the OMS. Orders are persisted
        as NEW->SUBMITTED here; fills arrive asynchronously via the postback
        webhook (qsdash.api.webhooks) which updates rows + publishes."""
        if not decision.orders:
            return
        mos = self.oms.submit_intents(decision.orders, self.instruments, ts, prices)
        sess = SessionLocal()
        if isinstance(self.publisher, PgSyncPublisher):
            self.publisher.bind(sess)
        try:
            for mo in mos:
                for s in mo.slices:
                    if not s.placed or not s.client_order_id:
                        continue
                    boid = self.oms._submitted.get(s.client_order_id, "")
                    row = OrderRow(
                        client_order_id=s.client_order_id, decision_id=decision_id,
                        ts=ts, mode=self.mode, strategy=mo.strategy, symbol=mo.symbol,
                        side=mo.side, qty=s.qty, style=mo.style.value,
                        urgency=mo.urgency.value, ref_price=prices.get(mo.symbol, 0.0),
                        status="SUBMITTED", broker_order_id=boid,
                        reason=f"live {mo.style.value}",
                        status_history=[{"ts": ts.isoformat(), "status": "SUBMITTED",
                                         "via": "oms"}],
                    )
                    sess.add(row)
                    self.publisher.publish("orders", {
                        "client_order_id": s.client_order_id, "symbol": mo.symbol,
                        "side": mo.side, "qty": s.qty, "status": "SUBMITTED",
                        "mode": self.mode, "broker_order_id": boid,
                    })
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            if isinstance(self.publisher, PgSyncPublisher):
                self.publisher.bind(None)
            sess.close()

    def pump(self, prices: dict[str, float]) -> None:
        """Advance scheduled slices (TWAP/AC) between bars."""
        try:
            self.oms.pump(self.instruments, prices)
        except BrokerError as e:
            log.error("oms pump failed: %s", e)

    def flatten_all(self, prices: dict[str, float], ts: datetime,
                    reason: str = "operator flatten") -> None:
        """Close every broker position at market via marketable orders."""
        from quantsys.core.types import ExecutionStyle, Urgency

        intents = []
        for sym, q in self.positions_map().items():
            if q == 0:
                continue
            intents.append(OrderIntent(
                symbol=sym, qty_delta=-q, style=ExecutionStyle.MARKET_SINGLE,
                urgency=Urgency.KILL, strategy="operator", reason=reason,
            ))
        if intents:
            self.oms.submit_intents(intents, self.instruments, ts, prices)
            log.warning("flatten_all submitted %d market exits (%s)", len(intents), reason)
