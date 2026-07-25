"""Core domain types shared by every layer (backtest, paper, live).

Design notes
------------
- Value objects are frozen dataclasses; anything stateful lives in engine
  components with explicit ``state_dict``/``load_state`` for persistence.
- Quantities are signed integers in instrument units (shares / contract
  units). Lot constraints are applied at sizing time via ``Instrument.lot_size``.
- Timestamps are timezone-naive and interpreted as exchange time (IST). The
  live data layer normalises to IST before anything reaches the core, so
  backtest and live traverse identical code paths.
- Multi-leg trades (pairs/spreads) are expressed as ONE ``Signal`` whose
  ``legs`` carry notional ratios relative to the parent leg. The sizing engine
  sizes the parent from the group's risk budget and derives the other legs, so
  hedge ratios survive every downstream cap (caps scale groups jointly).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

# --- NSE calendar constants ------------------------------------------------
TRADING_DAYS_PER_YEAR = 252
SESSION_MINUTES = 375  # 09:15-15:30 IST
SESSION_OPEN = (9, 15)
SESSION_CLOSE = (15, 30)

# Full-day NSE equity/F&O closures. Weekend-falling holidays are deliberately
# omitted — the weekday test in ``is_trading_day`` already covers them.
#
# Provenance: cross-checked against two independent published 2026 calendars on
# 2026-07-26 AND validated against our own recorded feed — every date below
# produced 100% zero-volume snapshot bars in ``market_bars`` (e.g. Muharram,
# Fri 2026-06-26: 350/350 synthetic, yet the engine still cut 11 fills on it).
#
# The Diwali Muhurat session (Sun 2026-11-08, ~1h in the evening) is treated as
# CLOSED: a symbolic one-hour Sunday session is not worth relaxing the weekend
# guard for, and no sleeve here has an edge that needs it.
#
# MAINTENANCE: append the next year's list from the official NSE circular each
# January (https://www.nseindia.com/resources/exchange-communication-holidays).
# A missing year degrades safely — holidays then fall back to the
# zero-volume/flat-bar guard rather than silently trading fabricated bars.
NSE_HOLIDAYS: frozenset[date] = frozenset({
    date(2026, 1, 15),   # Maharashtra municipal elections
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 26),   # Shri Ram Navami
    date(2026, 3, 31),   # Shri Mahavir Jayanti
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id
    date(2026, 6, 26),   # Muharram
    date(2026, 9, 14),   # Ganesh Chaturthi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali Balipratipada
    date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    date(2026, 12, 25),  # Christmas
})

# IST is the engine's single clock (see module docstring): every timestamp is
# naive and means exchange wall-clock. ``now_ist`` is what the live data layer
# stamps ticks/bars with so they bucket on the NSE session regardless of the
# host/container timezone — the VPS runs in UTC, and using a bare datetime.now()
# there silently shifts every bar by 5h30 and breaks session bucketing.
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Naive IST wall-clock — the one timestamp convention across the engine."""
    return datetime.now(IST).replace(tzinfo=None)


def bars_per_day(bar_minutes: float) -> float:
    return SESSION_MINUTES / bar_minutes


def bars_per_year(bar_minutes: float) -> float:
    return TRADING_DAYS_PER_YEAR * bars_per_day(bar_minutes)


def annualization_factor(bar_minutes: float) -> float:
    """sqrt(bars/year): multiply a per-bar vol by this to annualise."""
    return math.sqrt(bars_per_year(bar_minutes))


class InstrumentKind(str, Enum):
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    INDEX = "INDEX"


class ExecutionStyle(str, Enum):
    """How the OMS should work an order. Chosen by the capital tier."""

    MARKET_SINGLE = "MARKET_SINGLE"
    LIMIT_SINGLE = "LIMIT_SINGLE"
    LIMIT_SMART = "LIMIT_SMART"
    SLICE_TWAP = "SLICE_TWAP"
    ALMGREN_CHRISS = "ALMGREN_CHRISS"


class Urgency(str, Enum):
    NORMAL = "NORMAL"
    RISK_REDUCING = "RISK_REDUCING"  # stop hit / risk veto: cross the spread
    KILL = "KILL"                    # kill switch: flatten now


@dataclass(frozen=True)
class Instrument:
    symbol: str                 # canonical key used everywhere in the system
    token: str = ""             # broker instrument token (Angel One)
    exchange: str = "NSE"       # NSE / NFO / BSE
    kind: InstrumentKind = InstrumentKind.EQUITY
    lot_size: int = 1
    tick_size: float = 0.05
    point_value: float = 1.0    # INR P&L per 1.0 price move per unit qty
    sector: str | None = None
    adv: float | None = None    # average daily volume in units; refreshed daily
    margin_rate: float = 1.0    # fraction of notional blocked as margin


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class LegSpec:
    """One leg of a (possibly multi-leg) signal.

    ``notional_ratio`` is relative to the parent leg's notional. The parent leg
    is ratio +1.0. A classic pair 'long A / short B with hedge ratio beta' is
    legs [(A, +1.0), (B, -beta)] with Signal.direction = +1 for long-spread.
    """

    symbol: str
    notional_ratio: float


@dataclass(frozen=True)
class Signal:
    strategy: str
    symbol: str                       # parent leg symbol
    direction: float                  # conviction in [-1, +1]; sign = side
    stop_distance: float              # parent-leg price units; group-level stop
    legs: tuple[LegSpec, ...] = ()    # empty => single leg on ``symbol``
    expected_edge_R: float = 0.10     # conservative expected R-multiple / trade
    horizon_bars: int | None = None
    tag: str = ""

    @property
    def group_id(self) -> str:
        return f"{self.strategy}:{self.tag or self.symbol}"

    def resolved_legs(self) -> tuple[LegSpec, ...]:
        return self.legs if self.legs else (LegSpec(self.symbol, 1.0),)


@dataclass(frozen=True)
class TargetPosition:
    """Desired position for one symbol, attributed to one strategy/group."""

    symbol: str
    qty: int
    strategy: str
    group_id: str
    stop_distance: float = 0.0    # catastrophic per-leg backstop, price units
    ref_price: float = 0.0        # price used when the target was computed
    urgency: Urgency = Urgency.NORMAL


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float = 0.0


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    qty_delta: int                # signed change vs current position
    style: ExecutionStyle
    urgency: Urgency
    strategy: str = ""            # dominant strategy for attribution/audit
    reason: str = ""


@dataclass(frozen=True)
class RegimeState:
    label: str                            # calm_trend | calm_range | turbulent
    probs: Mapping[str, float]            # probability per label
    risk_scaler: float                    # multiplies total risk
    strategy_weights: Mapping[str, float] # multiplies per-strategy Kelly f
    source: str = "hmm"                   # hmm | fallback


@dataclass(frozen=True)
class AuditEvent:
    """One adjustment/veto in the decision pipeline. Full audit trail."""

    stage: str                  # e.g. "sizing", "risk.gross_cap", "orders"
    rule: str
    detail: str
    symbol: str | None = None
    before: float | None = None
    after: float | None = None


@dataclass(frozen=True)
class Decision:
    ts: datetime
    equity: float
    tier_name: str
    regime: RegimeState
    signals: tuple[Signal, ...]
    kelly: Mapping[str, float]
    vol_scaler: float
    risk_frac_eff: float
    targets: tuple[TargetPosition, ...]   # final, post-risk, lot-rounded
    orders: tuple[OrderIntent, ...]
    halted: bool = False
    kill_reason: str | None = None
    audit: tuple[AuditEvent, ...] = ()


@dataclass(frozen=True)
class Fill:
    ts: datetime
    symbol: str
    qty: int          # signed
    price: float
    strategy: str = ""
    fees: float = 0.0


def session_date(ts: datetime):
    """Trading-session date for IST-naive timestamps (no overnight session)."""
    return ts.date()


def is_trading_day(d: date | datetime) -> bool:
    """True iff ``d`` is an NSE trading day (weekday and not a full closure).

    Before 2026-07-26 the session test looked at the clock ONLY, so every
    Saturday, Sunday and exchange holiday between 09:15 and 15:30 counted as an
    open session. The engine therefore decided and "traded" on days the market
    was shut, against snapshot ticks the broker replays when it is closed: 32%
    of all recorded bars were zero-volume flat fabrications, and Forward Study 2
    burned real modelled fees on 4 Saturdays, 3 Sundays and a Muharram holiday.
    ``BarAggregator.on_tick`` rejects off-session ticks so no such bar even
    forms; a year missing from ``NSE_HOLIDAYS`` degrades to the pre-fix
    behaviour for that day only, never worse.
    """
    d = d.date() if isinstance(d, datetime) else d
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def is_session_open(ts: datetime) -> bool:
    if not is_trading_day(ts):
        return False
    t = (ts.hour, ts.minute)
    return SESSION_OPEN <= t < SESSION_CLOSE


def to_jsonable(obj: Any) -> Any:
    """Best-effort conversion of core types to JSON-serialisable values."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, Mapping):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj
