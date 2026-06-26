"""Order Management System: turns engine OrderIntents into broker orders,
tracks their lifecycle, enforces idempotency, and drives tier-aware execution
algos. Venue-agnostic — works against any Broker (SimBroker in tests,
AngelOneBroker live).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime

from quantsys.core.types import (
    ExecutionStyle,
    Instrument,
    OrderIntent,
    Urgency,
)
from quantsys.execution.broker import (
    Broker,
    BrokerError,
    BrokerOrder,
)

log = logging.getLogger("quantsys.oms")


@dataclass
class Slice:
    qty: int
    placed: bool = False
    client_order_id: str = ""


@dataclass
class ManagedOrder:
    """A parent intent and its child slices (TWAP/AC) or a single child."""

    parent_id: str
    symbol: str
    side: str
    total_qty: int
    style: ExecutionStyle
    urgency: Urgency
    strategy: str
    slices: list[Slice] = field(default_factory=list)
    created: datetime | None = None
    done: bool = False


class OMS:
    def __init__(self, broker: Broker, *, max_retries: int = 2,
                 slice_pause_s: float = 1.0, clock=time.monotonic):
        self.broker = broker
        self.max_retries = max_retries
        self.slice_pause_s = slice_pause_s
        self._clock = clock
        self.managed: dict[str, ManagedOrder] = {}
        # idempotency: deterministic client ids per (bar_ts, symbol, intent)
        self._submitted: dict[str, str] = {}  # client_order_id -> broker_order_id

    # --------------------------------------------------------- id helpers
    @staticmethod
    def client_id(bar_ts: datetime, symbol_index: int, seq: int) -> str:
        """Compact, deterministic id that fits Angel One's 20-char ordertag
        verbatim (no truncation -> postback correlation never breaks).
        Deterministic in (bar minute, symbol index, seq) so a retried decision
        re-derives the SAME id and cannot double-submit. 5-min decision clock
        makes minute resolution unique per bar; broker_order_id is the primary
        correlation key regardless."""
        stamp = bar_ts.strftime("%m%d%H%M")          # 8
        return f"Q{stamp}{symbol_index:02d}{seq:01d}"  # e.g. Q06120920010 (12)

    # ------------------------------------------------------ slice planning
    def _plan_slices(self, intent: OrderIntent, inst: Instrument,
                     adv: float | None) -> list[int]:
        """Split qty into child slices by execution style. Lot-aligned."""
        qty = abs(intent.qty_delta)
        lot = max(1, inst.lot_size)
        if intent.style in (ExecutionStyle.MARKET_SINGLE,
                            ExecutionStyle.LIMIT_SINGLE,
                            ExecutionStyle.LIMIT_SMART):
            return [qty]
        # TWAP / Almgren-Chriss: number of slices grows with size vs ADV
        total_lots = max(1, qty // lot)
        if adv and adv > 0:
            participation = qty / adv
            n = int(min(10, max(2, math.ceil(participation / 0.02))))  # ~2% ADV/slice
        else:
            n = 4
        n = min(n, total_lots)  # never more slices than lots
        if n <= 1:
            return [qty]
        base_lots = total_lots // n
        rem = total_lots - base_lots * n  # spread remainder across the first slices
        slices: list[int] = []
        for i in range(n):
            take = base_lots + (1 if i < rem else 0)
            slices.append(take * lot)
        return slices

    # ------------------------------------------------------------- submit
    def submit_intents(self, intents, instruments: dict[str, Instrument],
                       bar_ts: datetime, prices: dict[str, float]) -> list[ManagedOrder]:
        """Place all of a decision's intents. Risk-reducing / kill orders go
        first and as marketable; new risk uses the tier's execution style."""
        ordered = sorted(
            intents,
            key=lambda it: 0 if it.urgency in (Urgency.KILL, Urgency.RISK_REDUCING) else 1,
        )
        sym_index = {s: i for i, s in enumerate(sorted(instruments))}
        out: list[ManagedOrder] = []
        for seq, intent in enumerate(ordered):
            inst = instruments.get(intent.symbol)
            if inst is None or intent.qty_delta == 0:
                continue
            mo = self._submit_one(intent, inst, bar_ts, seq,
                                  sym_index.get(intent.symbol, 99), prices)
            if mo is not None:
                out.append(mo)
        return out

    def _submit_one(self, intent: OrderIntent, inst: Instrument,
                    bar_ts: datetime, seq: int, symbol_index: int,
                    prices: dict[str, float]) -> ManagedOrder | None:
        side = "BUY" if intent.qty_delta > 0 else "SELL"
        parent_id = self.client_id(bar_ts, symbol_index, seq)
        if parent_id in self.managed:
            return self.managed[parent_id]  # idempotent: already handled this bar

        slice_qtys = self._plan_slices(intent, inst, inst.adv)
        mo = ManagedOrder(
            parent_id=parent_id, symbol=intent.symbol, side=side,
            total_qty=abs(intent.qty_delta), style=intent.style,
            urgency=intent.urgency, strategy=intent.strategy,
            slices=[Slice(q) for q in slice_qtys], created=bar_ts,
        )
        self.managed[parent_id] = mo

        # immediate slices for single styles; schedulers place slice 0 now and
        # the rest on subsequent pump() calls
        self._place_slice(mo, 0, inst, prices)
        if intent.style in (ExecutionStyle.MARKET_SINGLE,
                            ExecutionStyle.LIMIT_SINGLE,
                            ExecutionStyle.LIMIT_SMART):
            mo.done = all(s.placed for s in mo.slices)
        return mo

    def _limit_for(self, mo: ManagedOrder, inst: Instrument,
                   prices: dict[str, float]) -> float | None:
        if mo.style == ExecutionStyle.MARKET_SINGLE:
            return None
        px = prices.get(mo.symbol)
        if px is None:
            return None
        tick = max(inst.tick_size, 1e-6)
        # cross the spread for urgency, else passive by one tick
        aggressive = mo.urgency in (Urgency.KILL, Urgency.RISK_REDUCING)
        sign = 1 if mo.side == "BUY" else -1
        offset = (1 if aggressive else -1) * sign * tick
        return round((px + offset) / tick) * tick

    def _place_slice(self, mo: ManagedOrder, idx: int, inst: Instrument,
                     prices: dict[str, float]) -> None:
        s = mo.slices[idx]
        if s.placed:
            return
        s.client_order_id = f"{mo.parent_id}-s{idx}"
        order = BrokerOrder(
            client_order_id=s.client_order_id, symbol=mo.symbol, side=mo.side,
            qty=s.qty, style=mo.style, urgency=mo.urgency,
            limit_price=self._limit_for(mo, inst, prices), strategy=mo.strategy,
            ts=mo.created,
        )
        for attempt in range(self.max_retries + 1):
            try:
                ack = self.broker.place(order)
                self._submitted[s.client_order_id] = ack.broker_order_id
                s.placed = True
                return
            except BrokerError as e:
                if not e.retryable or attempt == self.max_retries:
                    log.error("order place failed (%s) — NOT retrying: %s",
                              s.client_order_id, e)
                    return  # fail safe: leave unplaced, never spam the market
                time.sleep(0.2 * (attempt + 1))

    # ---------------------------------------------------------------- pump
    def pump(self, instruments: dict[str, Instrument],
             prices: dict[str, float]) -> None:
        """Advance scheduled (TWAP/AC) orders and refresh statuses. Call once
        per bar (or sub-bar tick) between decisions."""
        for mo in self.managed.values():
            if mo.done:
                continue
            inst = instruments.get(mo.symbol)
            if inst is None:
                continue
            for idx, s in enumerate(mo.slices):
                if not s.placed:
                    self._place_slice(mo, idx, inst, prices)
                    break  # one slice per pump => time-spreading
            mo.done = all(s.placed for s in mo.slices)

    def reconcile_statuses(self) -> dict[str, BrokerOrder]:
        out: dict[str, BrokerOrder] = {}
        for mo in self.managed.values():
            for s in mo.slices:
                if s.placed and s.client_order_id:
                    st = self.broker.order_status(s.client_order_id)
                    if st is not None:
                        out[s.client_order_id] = st
        return out
