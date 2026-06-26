"""Control plane. Every endpoint here:

1. requires CSRF + *fresh* re-authentication (password + TOTP within window),
2. writes ``config_versions`` (versioned diff) and/or a ``commands`` row,
3. audit-logs, 4. publishes a ``commands``/``config`` event.

The API NEVER mutates engine behaviour directly — the engine consumes the
command queue and acks. Switching to LIVE additionally demands the typed
confirmation phrase and refuses if paper positions are open (unless the
operator explicitly chose flatten_first).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from qsdash.audit import audit, raise_alert
from qsdash.bus import make_sync_publisher
from qsdash.config import settings
from qsdash.db import SessionLocal, now_ist
from qsdash.deps import client_ip, current_session, get_db, require_fresh_reauth
from qsdash.models import (
    MODE_LIVE,
    MODE_PAPER,
    AuthSession,
    Command,
    ConfigVersion,
    PositionRow,
    RuntimeConfig,
    User,
)

router = APIRouter(prefix="/control", tags=["control"])

_publisher = make_sync_publisher(SessionLocal)

# Operator-tunable risk keys the settings page may write. Anything outside
# this list is rejected — adding a knob is a deliberate code change.
ALLOWED_CONFIG_KEYS = {
    "sizing.base_risk_frac",
    "vol_target.annual_vol_target",
    "drawdown.daily_loss_limit",
    "drawdown.max_drawdown",
    "exposure.gross_cap",
    "exposure.net_cap",
    "exposure.instrument_cap",
    "exposure.sector_cap",
    "engine.min_order_notional",
}

PAPER_CAPITAL_MIN = 100_000        # Rs 1L
PAPER_CAPITAL_MAX = 200_000_000    # Rs 20cr


def _username(db: Session, sess: AuthSession) -> str:
    u = db.query(User).filter(User.id == sess.user_id).first()
    return u.username if u else "?"


def _get_rc(db: Session, key: str) -> Any:
    row = db.query(RuntimeConfig).filter(RuntimeConfig.key == key).first()
    return None if row is None else row.value.get("v")


def _set_rc(db: Session, key: str, value: Any, username: str, reason: str = "") -> None:
    row = db.query(RuntimeConfig).filter(RuntimeConfig.key == key).first()
    old = None if row is None else row.value
    if row is None:
        row = RuntimeConfig(key=key, value={"v": value}, updated_by=username)
        db.add(row)
    else:
        row.value = {"v": value}
        row.version += 1
        row.updated_at = now_ist()
        row.updated_by = username
    db.add(ConfigVersion(
        username=username, key=key, old_value=old, new_value={"v": value}, reason=reason
    ))


def _enqueue(db: Session, username: str, kind: str, payload: dict) -> Command:
    cmd = Command(created_by=username, kind=kind, payload=payload)
    db.add(cmd)
    db.flush()
    return cmd


def _publish_command(cmd: Command) -> None:
    _publisher.publish("commands", {
        "id": cmd.id, "kind": cmd.kind, "status": cmd.status, "payload": cmd.payload,
    })


# ------------------------------------------------------------------- bodies
class ModeBody(BaseModel):
    target_mode: Literal["paper", "live"]
    confirmation_phrase: str = ""
    flatten_first: bool = False
    # live-mode capital controls (required when going live)
    deployable_cap_frac: float | None = Field(None, ge=0.0, le=1.0)
    deployable_cap_abs: float | None = Field(None, ge=0.0)
    reason: str = ""


class PaperCapitalBody(BaseModel):
    capital: float = Field(ge=PAPER_CAPITAL_MIN, le=PAPER_CAPITAL_MAX)
    reason: str = ""


class CapBody(BaseModel):
    deployable_cap_frac: float | None = Field(None, ge=0.0, le=1.0)
    deployable_cap_abs: float | None = Field(None, ge=0.0)
    reason: str = ""


class KillBody(BaseModel):
    action: Literal["kill", "flatten", "rearm_dd_kill", "clear_halt"]
    reason: str = ""


class StrategyToggleBody(BaseModel):
    strategy: str
    enabled: bool
    reason: str = ""


class ConfigBody(BaseModel):
    key: str
    value: float
    reason: str = ""


class EngineBody(BaseModel):
    action: Literal["pause", "resume"]
    reason: str = ""


# ---------------------------------------------------------------- endpoints
@router.post("/mode")
def set_mode(body: ModeBody, request: Request, db: Session = Depends(get_db),
             sess: AuthSession = Depends(require_fresh_reauth)):
    username = _username(db, sess)
    current = _get_rc(db, "mode") or MODE_PAPER

    if body.target_mode == current:
        raise HTTPException(400, f"already in {current} mode")

    if body.target_mode == MODE_LIVE:
        # The four locks on the door to real money:
        # fresh re-auth (dependency), typed phrase, explicit cap, clean book.
        if body.confirmation_phrase != settings.live_confirmation_phrase:
            audit(db, username, "control.mode.rejected",
                  {"reason": "bad confirmation phrase"}, client_ip(request))
            db.commit()
            raise HTTPException(400, "confirmation phrase incorrect")
        if body.deployable_cap_frac is None and body.deployable_cap_abs is None:
            raise HTTPException(400, "a deployable-capital cap is required for live mode")
        open_paper = (
            db.query(PositionRow)
            .filter(PositionRow.mode == MODE_PAPER, PositionRow.status == "open")
            .count()
        )
        if open_paper > 0 and not body.flatten_first:
            raise HTTPException(
                409,
                f"{open_paper} paper positions are open; choose flatten_first "
                "or close them before going live",
            )

    if body.target_mode == MODE_PAPER:
        open_live = (
            db.query(PositionRow)
            .filter(PositionRow.mode == MODE_LIVE, PositionRow.status == "open")
            .count()
        )
        if open_live > 0 and not body.flatten_first:
            raise HTTPException(
                409,
                f"{open_live} LIVE positions are open; choose flatten_first "
                "(flatten at market) before leaving live mode",
            )

    payload: dict = {
        "target_mode": body.target_mode,
        "flatten_first": body.flatten_first,
        "from_mode": current,
    }
    if body.deployable_cap_frac is not None:
        _set_rc(db, "deployable_cap_frac", body.deployable_cap_frac, username, body.reason)
        payload["deployable_cap_frac"] = body.deployable_cap_frac
    if body.deployable_cap_abs is not None:
        _set_rc(db, "deployable_cap_abs", body.deployable_cap_abs, username, body.reason)
        payload["deployable_cap_abs"] = body.deployable_cap_abs

    # NOTE: 'mode' RuntimeConfig is flipped by the ENGINE when it completes the
    # transition (command ack), not here — the badge always shows engine truth.
    _set_rc(db, "mode_requested", body.target_mode, username, body.reason)
    cmd = _enqueue(db, username, "set_mode", payload)
    audit(db, username, "control.mode.requested", payload, client_ip(request))
    raise_alert(
        db, severity="crit" if body.target_mode == MODE_LIVE else "warn",
        kind="mode_change",
        title=f"Mode change requested: {current} -> {body.target_mode.upper()}",
        body=f"by {username}; cap_frac={body.deployable_cap_frac} "
             f"cap_abs={body.deployable_cap_abs} flatten_first={body.flatten_first}",
    )
    db.commit()
    _publish_command(cmd)
    return {"ok": True, "command_id": cmd.id, "status": "queued",
            "note": "engine will ack and complete the transition"}


@router.post("/paper-capital")
def set_paper_capital(body: PaperCapitalBody, request: Request,
                      db: Session = Depends(get_db),
                      sess: AuthSession = Depends(require_fresh_reauth)):
    username = _username(db, sess)
    _set_rc(db, "paper_capital", body.capital, username, body.reason)
    cmd = _enqueue(db, username, "set_paper_capital", {"capital": body.capital})
    audit(db, username, "control.paper_capital", {"capital": body.capital},
          client_ip(request))
    db.commit()
    _publish_command(cmd)
    return {"ok": True, "command_id": cmd.id}


@router.post("/deployable-cap")
def set_deployable_cap(body: CapBody, request: Request, db: Session = Depends(get_db),
                       sess: AuthSession = Depends(require_fresh_reauth)):
    if body.deployable_cap_frac is None and body.deployable_cap_abs is None:
        raise HTTPException(400, "provide deployable_cap_frac and/or deployable_cap_abs")
    username = _username(db, sess)
    payload: dict = {}
    if body.deployable_cap_frac is not None:
        _set_rc(db, "deployable_cap_frac", body.deployable_cap_frac, username, body.reason)
        payload["deployable_cap_frac"] = body.deployable_cap_frac
    if body.deployable_cap_abs is not None:
        _set_rc(db, "deployable_cap_abs", body.deployable_cap_abs, username, body.reason)
        payload["deployable_cap_abs"] = body.deployable_cap_abs
    cmd = _enqueue(db, username, "set_deployable_cap", payload)
    audit(db, username, "control.deployable_cap", payload, client_ip(request))
    db.commit()
    _publish_command(cmd)
    return {"ok": True, "command_id": cmd.id}


@router.post("/kill")
def kill_actions(body: KillBody, request: Request, db: Session = Depends(get_db),
                 sess: AuthSession = Depends(require_fresh_reauth)):
    username = _username(db, sess)
    cmd = _enqueue(db, username, body.action, {"reason": body.reason})
    audit(db, username, f"control.{body.action}", {"reason": body.reason},
          client_ip(request))
    sev = "crit" if body.action in ("kill", "flatten") else "warn"
    raise_alert(db, severity=sev, kind=body.action,
                title=f"Operator {body.action} requested",
                body=f"by {username}: {body.reason}")
    db.commit()
    _publish_command(cmd)
    return {"ok": True, "command_id": cmd.id}


@router.post("/strategy")
def toggle_strategy(body: StrategyToggleBody, request: Request,
                    db: Session = Depends(get_db),
                    sess: AuthSession = Depends(require_fresh_reauth)):
    username = _username(db, sess)
    key = f"strategy_enabled.{body.strategy}"
    _set_rc(db, key, body.enabled, username, body.reason)
    cmd = _enqueue(db, username, "strategy_toggle",
                   {"strategy": body.strategy, "enabled": body.enabled})
    audit(db, username, "control.strategy_toggle",
          {"strategy": body.strategy, "enabled": body.enabled}, client_ip(request))
    db.commit()
    _publish_command(cmd)
    return {"ok": True, "command_id": cmd.id}


@router.post("/config")
def set_config(body: ConfigBody, request: Request, db: Session = Depends(get_db),
               sess: AuthSession = Depends(require_fresh_reauth)):
    if body.key not in ALLOWED_CONFIG_KEYS:
        raise HTTPException(400, f"key not operator-tunable: {body.key}")
    username = _username(db, sess)
    _set_rc(db, body.key, body.value, username, body.reason)
    cmd = _enqueue(db, username, "set_config", {"key": body.key, "value": body.value})
    audit(db, username, "control.config", {"key": body.key, "value": body.value},
          client_ip(request))
    db.commit()
    _publish_command(cmd)
    return {"ok": True, "command_id": cmd.id}


@router.post("/engine")
def engine_control(body: EngineBody, request: Request, db: Session = Depends(get_db),
                   sess: AuthSession = Depends(require_fresh_reauth)):
    """Pause/resume the engine's decision loop. Pause HALTS new decisions WITHOUT
    flattening (use /kill to flatten); resume restarts it. Durable (persisted, so
    a pause survives a restart). Same CSRF + fresh-reauth + queue + audit + alert
    path as every other control; the engine acks via the command queue."""
    username = _username(db, sess)
    kind = "engine_pause" if body.action == "pause" else "engine_resume"
    cmd = _enqueue(db, username, kind, {"reason": body.reason})
    audit(db, username, f"control.engine.{body.action}",
          {"reason": body.reason}, client_ip(request))
    raise_alert(db, severity="warn", kind=f"engine_{body.action}",
                title=f"Engine {body.action} requested",
                body=f"by {username}: {body.reason}")
    db.commit()
    _publish_command(cmd)
    return {"ok": True, "command_id": cmd.id, "action": body.action}


@router.get("/commands")
def list_commands(limit: int = 50, db: Session = Depends(get_db),
                  sess: AuthSession = Depends(current_session)):
    rows = (db.query(Command).order_by(Command.id.desc()).limit(min(limit, 200)).all())
    return [{
        "id": c.id, "created_at": c.created_at.isoformat(), "created_by": c.created_by,
        "kind": c.kind, "payload": c.payload, "status": c.status,
        "acked_at": c.acked_at.isoformat() if c.acked_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        "result": c.result,
    } for c in rows]
