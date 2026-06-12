"""Recorder: persists every engine output to Postgres and publishes events in
the SAME transaction (NOTIFY is transactional; with Redis the publish happens
after commit). The dashboard reads only what lands here — single source of
truth, no parallel bookkeeping.
"""

from __future__ import annotations

import logging
import math
import os
import socket
from datetime import datetime

from sqlalchemy.orm import Session

from quantsys.core.types import Decision, to_jsonable

from qsdash.bus import PgSyncPublisher, SyncPublisher, make_sync_publisher
from qsdash.db import SessionLocal, now_ist
from qsdash.models import (
    DecisionRow,
    EngineStatus,
    EquityPoint,
    MarketBar,
    RegimeHistoryRow,
    RiskEvent,
    StrategyStat,
)

log = logging.getLogger(__name__)

# audit stages/rules that deserve a first-class risk_events row
_RISK_RULES = {"kill_switch", "halted", "stop_veto", "stop_cooldown"}
_SEV = {"kill_switch": "crit", "halted": "crit", "stop_veto": "warn",
        "stop_cooldown": "info"}


class Recorder:
    def __init__(self, mode: str, publisher: SyncPublisher | None = None):
        self.mode = mode
        self.publisher = publisher or make_sync_publisher(SessionLocal)

    # ------------------------------------------------------------ sessions
    def _begin(self) -> Session:
        sess = SessionLocal()
        if isinstance(self.publisher, PgSyncPublisher):
            self.publisher.bind(sess)
        return sess

    def _end(self, sess: Session) -> None:
        sess.commit()
        sess.close()
        if isinstance(self.publisher, PgSyncPublisher):
            self.publisher.bind(None)

    # ------------------------------------------------------------- writes
    def record_decision(self, decision: Decision, engine=None) -> int:
        """Persist one Decision + derived rows. Returns decisions.id."""
        d = to_jsonable(decision)
        sess = self._begin()
        try:
            row = DecisionRow(
                ts=decision.ts, mode=self.mode, equity=decision.equity,
                tier_name=decision.tier_name,
                regime_label=decision.regime.label,
                regime_probs=dict(decision.regime.probs),
                regime_source=decision.regime.source,
                risk_frac_eff=decision.risk_frac_eff,
                vol_scaler=decision.vol_scaler,
                kelly=dict(decision.kelly),
                halted=decision.halted, kill_reason=decision.kill_reason,
                signals=d["signals"], targets=d["targets"],
                orders=d["orders"], audit=d["audit"],
            )
            sess.add(row)
            sess.flush()

            if decision.regime.source != "none":
                sess.add(RegimeHistoryRow(
                    ts=decision.ts, label=decision.regime.label,
                    probs=dict(decision.regime.probs),
                    risk_scaler=decision.regime.risk_scaler,
                    strategy_weights=dict(decision.regime.strategy_weights),
                    source=decision.regime.source,
                ))

            for ev in decision.audit:
                if ev.rule in _RISK_RULES:
                    sess.add(RiskEvent(
                        ts=decision.ts, mode=self.mode, kind=ev.rule,
                        rule=ev.rule, symbol=ev.symbol,
                        severity=_SEV.get(ev.rule, "warn"),
                        cause=ev.detail, decision_id=row.id,
                        detail={"stage": ev.stage, "before": ev.before,
                                "after": ev.after},
                    ))

            if engine is not None:
                from quantsys.core.types import annualization_factor

                ann = annualization_factor(engine.cfg.engine.decision_bar_minutes)
                for name, stats in engine.edge_stats.items():
                    mu, var = stats.mean, stats.var  # shrunk mean = allocator's view
                    sharpe = (mu / math.sqrt(var) * ann) if var > 0 else 0.0
                    sess.add(StrategyStat(
                        ts=decision.ts, strategy=name, mu=mu, var=var,
                        n_eff=stats.n_eff,
                        kelly_f=decision.kelly.get(name, 0.0),
                        sharpe_ann=sharpe,
                        incubating=stats.n_eff < engine.cfg.kelly.ramp_obs,
                    ))

            self.publisher.publish("decisions", {
                "id": row.id, "ts": decision.ts.isoformat(), "mode": self.mode,
                "equity": decision.equity, "tier": decision.tier_name,
                "regime": decision.regime.label,
                "regime_probs": dict(decision.regime.probs),
                "vol_scaler": decision.vol_scaler,
                "risk_frac_eff": decision.risk_frac_eff,
                "halted": decision.halted, "kill_reason": decision.kill_reason,
                "n_orders": len(decision.orders),
            })
            if decision.regime.source != "none":
                self.publisher.publish("regime", {
                    "ts": decision.ts.isoformat(), "label": decision.regime.label,
                    "probs": dict(decision.regime.probs),
                    "risk_scaler": decision.regime.risk_scaler,
                    "source": decision.regime.source,
                })
            for ev in decision.audit:
                if ev.rule in _RISK_RULES:
                    self.publisher.publish("risk", {
                        "ts": decision.ts.isoformat(), "kind": ev.rule,
                        "symbol": ev.symbol, "cause": ev.detail,
                        "severity": _SEV.get(ev.rule, "warn"),
                    })
            self._end(sess)
            return row.id
        except Exception:
            sess.rollback()
            sess.close()
            raise

    def record_equity(self, ts: datetime, *, equity: float, cash: float,
                      mtm: float, gross: float, net: float,
                      realized: float, unrealized: float) -> None:
        sess = self._begin()
        try:
            sess.add(EquityPoint(
                ts=ts, mode=self.mode, equity=equity, cash=cash, mtm=mtm,
                gross_exposure=gross, net_exposure=net,
                realized_pnl=realized, unrealized_pnl=unrealized,
            ))
            self.publisher.publish("equity", {
                "ts": ts.isoformat(), "mode": self.mode, "equity": equity,
                "cash": cash, "gross_exposure": gross, "net_exposure": net,
                "realized_pnl": realized, "unrealized_pnl": unrealized,
            })
            self._end(sess)
        except Exception:
            sess.rollback()
            sess.close()
            raise

    def record_bars(self, tf_minutes: int, bars: dict) -> None:
        """One transaction per decision step for the whole bar batch."""
        if not bars:
            return
        sess = self._begin()
        try:
            for symbol, bar in bars.items():
                sess.merge(MarketBar(
                    symbol=symbol, tf_minutes=tf_minutes, ts=bar.ts,
                    open=bar.open, high=bar.high, low=bar.low,
                    close=bar.close, volume=bar.volume,
                ))
            self._end(sess)
        except Exception:
            sess.rollback()
            sess.close()
            raise

    def heartbeat(self, *, status: str, market_open: bool,
                  detail: dict | None = None) -> None:
        sess = self._begin()
        try:
            row = sess.get(EngineStatus, 1)
            if row is None:
                row = EngineStatus(id=1)
                sess.add(row)
            row.last_heartbeat = now_ist()
            row.mode = self.mode
            row.host = socket.gethostname()
            row.pid = os.getpid()
            row.market_open = market_open
            row.status = status
            row.detail = detail or {}
            self.publisher.publish("engine", {
                "ts": row.last_heartbeat.isoformat(), "status": status,
                "mode": self.mode, "market_open": market_open,
                "detail": detail or {},
            })
            self._end(sess)
        except Exception:
            sess.rollback()
            sess.close()
            raise
