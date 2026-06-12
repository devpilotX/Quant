"""The Broker interface and its value types.

Contract notes:
- `place` is idempotent on `client_order_id`: re-submitting a known id returns
  the existing ack, never a duplicate order. Callers generate stable ids.
- Quantities are signed instrument units everywhere (engine convention).
- All methods may raise BrokerError; the live runner treats any error as
  "reduce risk / do nothing", never "retry blindly into the market".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from quantsys.core.types import ExecutionStyle, Instrument, Urgency


class OrderStatus(str, Enum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELLED,
                        OrderStatus.REJECTED)


class BrokerError(Exception):
    """Any broker-side failure. Carries whether a retry is safe."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class BrokerOrder:
    client_order_id: str
    symbol: str
    side: str                 # BUY / SELL
    qty: int                  # absolute units requested
    style: ExecutionStyle
    urgency: Urgency
    limit_price: float | None = None
    broker_order_id: str = ""
    status: OrderStatus = OrderStatus.NEW
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    reject_reason: str = ""
    strategy: str = ""
    ts: datetime | None = None


@dataclass
class OrderAck:
    client_order_id: str
    broker_order_id: str
    status: OrderStatus
    detail: str = ""


@dataclass
class BrokerPosition:
    symbol: str
    qty: int                  # signed
    avg_price: float = 0.0


@dataclass
class TickerQuote:
    symbol: str
    ltp: float
    ts: datetime
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0


class Broker(Protocol):
    """Everything the live runner needs from a venue."""

    def connect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def funds(self) -> float:
        """Available trading capital (net) in INR."""
        ...

    def instruments(self) -> dict[str, Instrument]:
        """Tradeable universe with live lot/tick/margin from the master."""
        ...

    def positions(self) -> list[BrokerPosition]:
        """Broker's view of holdings — ground truth for reconciliation."""
        ...

    def open_orders(self) -> list[BrokerOrder]: ...

    def place(self, order: BrokerOrder) -> OrderAck:
        """Idempotent on order.client_order_id."""
        ...

    def cancel(self, client_order_id: str) -> OrderAck: ...

    def order_status(self, client_order_id: str) -> BrokerOrder | None: ...
