"""Database schema — every number the dashboard shows lives in one of these
tables, written by the engine (trading truth) or the API (auth/control).

Conventions
-----------
- ``mode`` columns separate PAPER and LIVE universes everywhere; they are
  never aggregated together.
- JSON columns are JSONB on Postgres, plain JSON elsewhere (tests on SQLite).
- ``decisions.audit`` holds the engine's full AuditEvent trail verbatim —
  the explainability views render it, they never re-derive it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from qsdash.db import Base, now_ist

JSONVariant = JSON().with_variant(JSONB(), "postgresql")
# SQLite autoincrement requires INTEGER PRIMARY KEY; BIGINT stays for postgres
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

MODE_PAPER = "paper"
MODE_LIVE = "live"


# ============================================================ auth & control
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    totp_secret: Mapped[str] = mapped_column(String(64))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    reauth_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    csrf_token: Mapped[str] = mapped_column(String(64), default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")


class RuntimeConfig(Base):
    """Current value of every operator-controllable knob.

    The engine reads this each decision loop; the API writes it only through
    the control endpoints (which also append to config_versions + commands).
    Keys: mode, paper_capital, deployable_cap_frac, deployable_cap_abs,
    strategy_enabled.<name>, risk overrides...
    """

    __tablename__ = "runtime_config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONVariant)  # {"v": <actual value>}
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")


class ConfigVersion(Base):
    __tablename__ = "config_versions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
    username: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128), index=True)
    old_value: Mapped[dict | None] = mapped_column(JSONVariant, default=None)
    new_value: Mapped[dict] = mapped_column(JSONVariant)
    reason: Mapped[str] = mapped_column(Text, default="")


class Command(Base):
    """Operator -> engine command queue. The dashboard NEVER bypasses this."""

    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64), index=True)
    # kinds: set_mode, set_paper_capital, set_deployable_cap, kill, flatten,
    #        rearm_dd_kill, strategy_toggle, set_config, clear_halt
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending -> acked -> done | rejected
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    result: Mapped[dict | None] = mapped_column(JSONVariant, default=None)


# ============================================================ trading truth
class DecisionRow(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    equity: Mapped[float] = mapped_column(Float)
    tier_name: Mapped[str] = mapped_column(String(16))
    regime_label: Mapped[str] = mapped_column(String(32))
    regime_probs: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    regime_source: Mapped[str] = mapped_column(String(16), default="hmm")
    risk_frac_eff: Mapped[float] = mapped_column(Float, default=0.0)
    vol_scaler: Mapped[float] = mapped_column(Float, default=1.0)
    kelly: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    halted: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    signals: Mapped[list] = mapped_column(JSONVariant, default=list)
    targets: Mapped[list] = mapped_column(JSONVariant, default=list)
    orders: Mapped[list] = mapped_column(JSONVariant, default=list)
    audit: Mapped[list] = mapped_column(JSONVariant, default=list)

    __table_args__ = (Index("ix_decisions_mode_ts", "mode", "ts"),)


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    decision_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("decisions.id"), default=None
    )
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    side: Mapped[str] = mapped_column(String(4))  # BUY / SELL
    qty: Mapped[int] = mapped_column(Integer)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    style: Mapped[str] = mapped_column(String(24), default="MARKET_SINGLE")
    urgency: Mapped[str] = mapped_column(String(16), default="NORMAL")
    limit_price: Mapped[float | None] = mapped_column(Float, default=None)
    ref_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="NEW", index=True)
    # NEW -> SUBMITTED -> PARTIAL -> FILLED | CANCELLED | REJECTED
    broker_order_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    reject_reason: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")  # engine OrderIntent.reason
    status_history: Mapped[list] = mapped_column(JSONVariant, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)

    __table_args__ = (Index("ix_orders_mode_ts", "mode", "ts"),)


class FillRow(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("orders.id"), default=None
    )
    client_order_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    qty: Mapped[int] = mapped_column(Integer)  # signed
    price: Mapped[float] = mapped_column(Float)
    fees_total: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)  # vs intended px


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    qty: Mapped[int] = mapped_column(Integer)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    stop_distance: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    entry_decision_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    exit_decision_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    entry_rationale: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    exit_rationale: Mapped[dict] = mapped_column(JSONVariant, default=dict)

    __table_args__ = (Index("ix_positions_mode_status", "mode", "status"),)


class EquityPoint(Base):
    __tablename__ = "equity_curve"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    mtm: Mapped[float] = mapped_column(Float, default=0.0)
    gross_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    net_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (Index("ix_equity_mode_ts", "mode", "ts"),)


class PnlAttribution(Base):
    __tablename__ = "pnl_attribution"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)  # session date
    mode: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(64), default="")
    regime: Mapped[str] = mapped_column(String(32), default="")
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    n_trades: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("date", "mode", "strategy", "symbol", "regime",
                         name="uq_pnl_attr_bucket"),
    )


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    # kill_switch, stop_veto, stop_cooldown, cap_hit, reconciliation_halt,
    # heartbeat_lost, feed_lost, ...
    rule: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str | None] = mapped_column(String(64), default=None)
    severity: Mapped[str] = mapped_column(String(8), default="warn")  # info|warn|crit
    cause: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    decision_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    acknowledged_by: Mapped[str] = mapped_column(String(64), default="")


class RegimeHistoryRow(Base):
    __tablename__ = "regime_history"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    label: Mapped[str] = mapped_column(String(32))
    probs: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    risk_scaler: Mapped[float] = mapped_column(Float, default=1.0)
    strategy_weights: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    source: Mapped[str] = mapped_column(String(16), default="hmm")


class MarketBar(Base):
    """Bars for the active universe. On the VPS this becomes a Timescale
    hypertable (see migration); locally it is a plain indexed table."""

    __tablename__ = "market_bars"

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    tf_minutes: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)


class StrategyStat(Base):
    __tablename__ = "strategy_stats"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    mu: Mapped[float] = mapped_column(Float, default=0.0)        # EWMA edge / bar
    var: Mapped[float] = mapped_column(Float, default=0.0)
    n_eff: Mapped[float] = mapped_column(Float, default=0.0)
    kelly_f: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ann: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    incubating: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
    severity: Mapped[str] = mapped_column(String(8), default="info")
    kind: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    channels: Mapped[list] = mapped_column(JSONVariant, default=list)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_detail: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class WebhookEvent(Base):
    """Inbound webhook ledger — the idempotency guard."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # angelone
    external_id: Mapped[str] = mapped_column(String(128))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="received")
    # received -> processed | ignored | error
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    error: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_webhook_source_ext"),
    )


class EngineStatus(Base):
    """Single-row (id=1) heartbeat target written by the engine every loop."""

    __tablename__ = "engine_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    mode: Mapped[str] = mapped_column(String(8), default=MODE_PAPER)
    host: Mapped[str] = mapped_column(String(128), default="")
    pid: Mapped[int] = mapped_column(Integer, default=0)
    market_open: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="stopped")
    # running | idle (market closed) | halted | killed | stopped
    detail: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    git_rev: Mapped[str] = mapped_column(String(64), default="")
    params: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    # {sharpe, sharpe_deflated, sortino, calmar, max_dd, hit_rate, ...} IS vs OOS
    equity_curve: Mapped[list] = mapped_column(JSONVariant, default=list)
    artifacts: Mapped[dict] = mapped_column(JSONVariant, default=dict)
