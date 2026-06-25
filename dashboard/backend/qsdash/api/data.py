"""Read-only snapshot endpoints for every dashboard view.

All numbers come straight from tables the engine wrote. Staleness is explicit:
/overview includes engine heartbeat age and the UI must render stale state as
stale, never as live.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from qsdash.db import now_ist
from qsdash.deps import current_session, get_db
from qsdash.models import (
    MODE_PAPER,
    Alert,
    AuditLog,
    BacktestRun,
    ConfigVersion,
    DecisionRow,
    EngineStatus,
    EquityPoint,
    FillRow,
    MarketBar,
    OrderRow,
    PnlAttribution,
    PositionRow,
    RegimeHistoryRow,
    RiskEvent,
    RuntimeConfig,
    StrategyStat,
)

router = APIRouter(tags=["data"], dependencies=[Depends(current_session)])

HEARTBEAT_STALE_SECONDS = 30


def _mode(db: Session) -> str:
    row = db.query(RuntimeConfig).filter(RuntimeConfig.key == "mode").first()
    return (row.value.get("v") if row else None) or MODE_PAPER


def _rc_all(db: Session) -> dict:
    return {r.key: r.value.get("v") for r in db.query(RuntimeConfig).all()}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- overview
@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    mode = _mode(db)
    rc = _rc_all(db)
    es = db.query(EngineStatus).filter(EngineStatus.id == 1).first()
    now = now_ist()
    hb_age = None
    if es is not None and es.last_heartbeat is not None:
        hb_age = (now - es.last_heartbeat).total_seconds()

    last_eq = (
        db.query(EquityPoint).filter(EquityPoint.mode == mode)
        .order_by(EquityPoint.ts.desc()).first()
    )
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first_eq_today = (
        db.query(EquityPoint)
        .filter(EquityPoint.mode == mode, EquityPoint.ts >= day_start)
        .order_by(EquityPoint.ts.asc()).first()
    )
    today_pnl = None
    if last_eq and first_eq_today:
        today_pnl = last_eq.equity - first_eq_today.equity

    open_positions = (
        db.query(PositionRow)
        .filter(PositionRow.mode == mode, PositionRow.status == "open").count()
    )
    last_dec = (
        db.query(DecisionRow).filter(DecisionRow.mode == mode)
        .order_by(DecisionRow.ts.desc()).first()
    )
    recent_alerts = db.query(Alert).order_by(Alert.id.desc()).limit(8).all()
    recent_risk = db.query(RiskEvent).order_by(RiskEvent.id.desc()).limit(8).all()

    return {
        "server_ts": now.isoformat(),
        "mode": mode,
        "mode_requested": rc.get("mode_requested"),
        "engine": {
            "status": es.status if es else "stopped",
            "heartbeat_age_s": hb_age,
            "stale": hb_age is None or hb_age > HEARTBEAT_STALE_SECONDS,
            "market_open": es.market_open if es else False,
            "host": es.host if es else "",
            "detail": es.detail if es else {},
        },
        "equity": {
            "value": last_eq.equity if last_eq else None,
            "ts": _iso(last_eq.ts) if last_eq else None,
            "cash": last_eq.cash if last_eq else None,
            "gross_exposure": last_eq.gross_exposure if last_eq else None,
            "net_exposure": last_eq.net_exposure if last_eq else None,
            "unrealized_pnl": last_eq.unrealized_pnl if last_eq else None,
            "today_pnl": today_pnl,
        },
        "open_positions": open_positions,
        "decision": None if last_dec is None else {
            "id": last_dec.id, "ts": _iso(last_dec.ts),
            "tier": last_dec.tier_name, "regime": last_dec.regime_label,
            "regime_probs": last_dec.regime_probs,
            "vol_scaler": last_dec.vol_scaler,
            "risk_frac_eff": last_dec.risk_frac_eff,
            "halted": last_dec.halted, "kill_reason": last_dec.kill_reason,
        },
        "capital_controls": {
            "paper_capital": rc.get("paper_capital"),
            "deployable_cap_frac": rc.get("deployable_cap_frac"),
            "deployable_cap_abs": rc.get("deployable_cap_abs"),
        },
        "recent_alerts": [{
            "id": a.id, "ts": _iso(a.ts), "severity": a.severity,
            "kind": a.kind, "title": a.title, "delivered": a.delivered,
        } for a in recent_alerts],
        "recent_risk_events": [{
            "id": r.id, "ts": _iso(r.ts), "kind": r.kind, "rule": r.rule,
            "severity": r.severity, "cause": r.cause, "symbol": r.symbol,
        } for r in recent_risk],
    }


# ------------------------------------------------------- positions & orders
@router.get("/positions")
def positions(status: str = "open", mode: str | None = None, limit: int = 200,
              db: Session = Depends(get_db)):
    mode = mode or _mode(db)
    q = db.query(PositionRow).filter(PositionRow.mode == mode)
    if status in ("open", "closed"):
        q = q.filter(PositionRow.status == status)
    rows = q.order_by(PositionRow.opened_at.desc()).limit(min(limit, 1000)).all()

    # live MTM from the latest bar per symbol
    out = []
    for p in rows:
        last_close = (
            db.query(MarketBar.close).filter(MarketBar.symbol == p.symbol)
            .order_by(MarketBar.ts.desc()).limit(1).scalar()
        )
        unreal = None
        if p.status == "open" and last_close is not None:
            unreal = (last_close - p.avg_price) * p.qty
        out.append({
            "id": p.id, "mode": p.mode, "symbol": p.symbol, "strategy": p.strategy,
            "qty": p.qty, "avg_price": p.avg_price, "last_price": last_close,
            "unrealized_pnl": unreal, "realized_pnl": p.realized_pnl,
            "fees_paid": p.fees_paid, "stop_distance": p.stop_distance,
            "status": p.status, "opened_at": _iso(p.opened_at),
            "closed_at": _iso(p.closed_at),
            "entry_decision_id": p.entry_decision_id,
            "exit_decision_id": p.exit_decision_id,
            "entry_rationale": p.entry_rationale,
            "exit_rationale": p.exit_rationale,
        })
    return out


@router.get("/orders")
def orders(status: str | None = None, symbol: str | None = None,
           mode: str | None = None, limit: int = 200, offset: int = 0,
           db: Session = Depends(get_db)):
    mode = mode or _mode(db)
    q = db.query(OrderRow).filter(OrderRow.mode == mode)
    if status:
        q = q.filter(OrderRow.status == status.upper())
    if symbol:
        q = q.filter(OrderRow.symbol == symbol)
    total = q.count()
    rows = (q.order_by(OrderRow.ts.desc())
            .offset(offset).limit(min(limit, 500)).all())
    return {"total": total, "rows": [{
        "id": o.id, "client_order_id": o.client_order_id, "ts": _iso(o.ts),
        "decision_id": o.decision_id, "strategy": o.strategy, "symbol": o.symbol,
        "side": o.side, "qty": o.qty, "filled_qty": o.filled_qty,
        "style": o.style, "urgency": o.urgency, "limit_price": o.limit_price,
        "ref_price": o.ref_price, "status": o.status,
        "broker_order_id": o.broker_order_id, "reject_reason": o.reject_reason,
        "reason": o.reason, "status_history": o.status_history,
    } for o in rows]}


@router.get("/fills")
def fills(limit: int = 200, mode: str | None = None, db: Session = Depends(get_db)):
    mode = mode or _mode(db)
    rows = (db.query(FillRow).filter(FillRow.mode == mode)
            .order_by(FillRow.ts.desc()).limit(min(limit, 500)).all())
    return [{
        "id": f.id, "ts": _iso(f.ts), "symbol": f.symbol, "strategy": f.strategy,
        "qty": f.qty, "price": f.price, "fees_total": f.fees_total,
        "fees": f.fees, "slippage": f.slippage, "client_order_id": f.client_order_id,
    } for f in rows]


# ------------------------------------------------------------ P&L & metrics
@router.get("/equity-curve")
def equity_curve(mode: str | None = None,
                 since: datetime | None = None,
                 max_points: int = Query(2000, le=10000),
                 db: Session = Depends(get_db)):
    mode = mode or _mode(db)
    q = db.query(EquityPoint).filter(EquityPoint.mode == mode)
    if since is not None:
        q = q.filter(EquityPoint.ts >= since)
    n = q.count()
    stride = max(1, n // max_points)
    rows = q.order_by(EquityPoint.ts.asc()).all()[::stride]
    return [{
        "ts": _iso(r.ts), "equity": r.equity, "cash": r.cash,
        "gross_exposure": r.gross_exposure, "net_exposure": r.net_exposure,
        "realized_pnl": r.realized_pnl, "unrealized_pnl": r.unrealized_pnl,
    } for r in rows]


@router.get("/pnl/attribution")
def pnl_attribution(by: str = "strategy", mode: str | None = None,
                    since: datetime | None = None, db: Session = Depends(get_db)):
    mode = mode or _mode(db)
    col = {
        "strategy": PnlAttribution.strategy,
        "symbol": PnlAttribution.symbol,
        "regime": PnlAttribution.regime,
        "day": PnlAttribution.date,
    }.get(by)
    if col is None:
        raise HTTPException(400, "by must be strategy|symbol|regime|day")
    q = (db.query(col.label("bucket"),
                  func.sum(PnlAttribution.gross_pnl).label("gross"),
                  func.sum(PnlAttribution.fees).label("fees"),
                  func.sum(PnlAttribution.net_pnl).label("net"),
                  func.sum(PnlAttribution.n_trades).label("n_trades"))
         .filter(PnlAttribution.mode == mode))
    if since is not None:
        q = q.filter(PnlAttribution.date >= since)
    rows = q.group_by(col).order_by(col).all()
    return [{
        "bucket": r.bucket.isoformat() if isinstance(r.bucket, datetime) else r.bucket,
        "gross_pnl": float(r.gross or 0), "fees": float(r.fees or 0),
        "net_pnl": float(r.net or 0), "n_trades": int(r.n_trades or 0),
    } for r in rows]


@router.get("/pnl/metrics")
def pnl_metrics(mode: str | None = None, db: Session = Depends(get_db)):
    """Performance metrics computed from the stored equity curve + fills.
    Daily resampling; all formulas standard. Returns null when insufficient data."""
    mode = mode or _mode(db)
    rows = (db.query(EquityPoint).filter(EquityPoint.mode == mode)
            .order_by(EquityPoint.ts.asc()).all())
    if len(rows) < 3:
        return {"insufficient_data": True, "n_points": len(rows)}

    # daily last-equity series
    daily: dict = {}
    for r in rows:
        daily[r.ts.date()] = r.equity
    eq = list(daily.values())
    rets = [(b - a) / a for a, b in zip(eq, eq[1:]) if a > 0]
    out: dict = {"n_days": len(eq), "insufficient_data": len(rets) < 2}

    peak, max_dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    out["max_drawdown"] = max_dd
    out["current_drawdown"] = (peak - eq[-1]) / peak if peak > 0 else 0.0
    out["total_return"] = (eq[-1] - eq[0]) / eq[0] if eq[0] > 0 else None

    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        downside = [min(0.0, x) for x in rets]
        dvar = sum(x * x for x in downside) / len(rets)
        dsd = math.sqrt(dvar)
        ann = 252
        out["sharpe"] = (mean / sd * math.sqrt(ann)) if sd > 0 else None
        out["sortino"] = (mean / dsd * math.sqrt(ann)) if dsd > 0 else None
        cagr = (1 + mean) ** ann - 1
        out["calmar"] = (cagr / max_dd) if max_dd > 0 else None
        out["ann_return_est"] = cagr
        out["ann_vol_est"] = sd * math.sqrt(ann)

    closed = (db.query(PositionRow)
              .filter(PositionRow.mode == mode, PositionRow.status == "closed").all())
    wins = [p for p in closed if p.realized_pnl > 0]
    losses = [p for p in closed if p.realized_pnl < 0]
    out["n_closed_trades"] = len(closed)
    out["hit_rate"] = len(wins) / len(closed) if closed else None
    gp = sum(p.realized_pnl for p in wins)
    gl = -sum(p.realized_pnl for p in losses)
    out["profit_factor"] = (gp / gl) if gl > 0 else None
    total_fees = db.query(func.sum(FillRow.fees_total)).filter(FillRow.mode == mode).scalar() or 0.0
    gross_traded = db.query(
        func.sum(func.abs(FillRow.qty) * FillRow.price)
    ).filter(FillRow.mode == mode).scalar() or 0.0
    out["total_fees"] = float(total_fees)
    out["turnover_notional"] = float(gross_traded)
    out["cost_drag_bps"] = (float(total_fees) / float(gross_traded) * 1e4) if gross_traded else None
    return out


# ------------------------------------------------- decisions & explainability
@router.get("/decisions")
def decisions(limit: int = 50, offset: int = 0, mode: str | None = None,
              db: Session = Depends(get_db)):
    mode = mode or _mode(db)
    q = db.query(DecisionRow).filter(DecisionRow.mode == mode)
    total = q.count()
    rows = (q.order_by(DecisionRow.ts.desc())
            .offset(offset).limit(min(limit, 200)).all())
    return {"total": total, "rows": [{
        "id": d.id, "ts": _iso(d.ts), "equity": d.equity, "tier": d.tier_name,
        "regime": d.regime_label, "regime_source": d.regime_source,
        "vol_scaler": d.vol_scaler, "risk_frac_eff": d.risk_frac_eff,
        "halted": d.halted, "kill_reason": d.kill_reason,
        "n_signals": len(d.signals or []), "n_orders": len(d.orders or []),
        "n_audit": len(d.audit or []),
    } for d in rows]}


@router.get("/decisions/{decision_id}")
def decision_detail(decision_id: int, db: Session = Depends(get_db)):
    d = db.query(DecisionRow).filter(DecisionRow.id == decision_id).first()
    if d is None:
        raise HTTPException(404, "decision not found")
    return {
        "id": d.id, "ts": _iso(d.ts), "mode": d.mode, "equity": d.equity,
        "tier": d.tier_name, "regime": d.regime_label,
        "regime_probs": d.regime_probs, "regime_source": d.regime_source,
        "risk_frac_eff": d.risk_frac_eff, "vol_scaler": d.vol_scaler,
        "kelly": d.kelly, "halted": d.halted, "kill_reason": d.kill_reason,
        "signals": d.signals, "targets": d.targets, "orders": d.orders,
        "audit": d.audit,
    }


@router.get("/trades/{position_id}/explain")
def explain_trade(position_id: int, db: Session = Depends(get_db)):
    """The 'why?' drill-down: entry + exit story assembled from decisions."""
    p = db.query(PositionRow).filter(PositionRow.id == position_id).first()
    if p is None:
        raise HTTPException(404, "position not found")

    def _dec(decision_id: int | None):
        if decision_id is None:
            return None
        d = db.query(DecisionRow).filter(DecisionRow.id == decision_id).first()
        if d is None:
            return None
        sym_audit = [a for a in (d.audit or [])
                     if a.get("symbol") in (None, p.symbol)]
        sym_signals = [s for s in (d.signals or []) if (
            s.get("symbol") == p.symbol
            or any(leg.get("symbol") == p.symbol for leg in (s.get("legs") or []))
        )]
        return {
            "id": d.id, "ts": _iso(d.ts), "equity": d.equity, "tier": d.tier_name,
            "regime": d.regime_label, "regime_probs": d.regime_probs,
            "regime_source": d.regime_source, "risk_frac_eff": d.risk_frac_eff,
            "vol_scaler": d.vol_scaler, "kelly": d.kelly,
            "signals_for_symbol": sym_signals,
            "audit_for_symbol": sym_audit,
            "full_audit": d.audit,
        }

    f_rows = (db.query(FillRow)
              .filter(FillRow.mode == p.mode, FillRow.symbol == p.symbol,
                      FillRow.ts >= p.opened_at)
              .order_by(FillRow.ts.asc()).all())
    if p.closed_at is not None:
        f_rows = [f for f in f_rows if f.ts <= p.closed_at]

    return {
        "position": {
            "id": p.id, "mode": p.mode, "symbol": p.symbol, "strategy": p.strategy,
            "qty": p.qty, "avg_price": p.avg_price, "status": p.status,
            "opened_at": _iso(p.opened_at), "closed_at": _iso(p.closed_at),
            "realized_pnl": p.realized_pnl, "fees_paid": p.fees_paid,
            "stop_distance": p.stop_distance,
        },
        "entry": {"rationale": p.entry_rationale, "decision": _dec(p.entry_decision_id)},
        "exit": {"rationale": p.exit_rationale, "decision": _dec(p.exit_decision_id)},
        "fills": [{
            "ts": _iso(f.ts), "qty": f.qty, "price": f.price,
            "fees_total": f.fees_total, "fees": f.fees, "slippage": f.slippage,
        } for f in f_rows],
    }


# ------------------------------------------------------ strategies & regime
@router.get("/strategies")
def strategies(db: Session = Depends(get_db)):
    rc = _rc_all(db)
    names = (db.query(StrategyStat.strategy).distinct().all())
    out = []
    for (name,) in names:
        last = (db.query(StrategyStat).filter(StrategyStat.strategy == name)
                .order_by(StrategyStat.ts.desc()).first())
        enabled = rc.get(f"strategy_enabled.{name}")
        out.append({
            "strategy": name,
            "enabled": True if enabled is None else bool(enabled),
            "ts": _iso(last.ts), "mu": last.mu, "var": last.var,
            "n_eff": last.n_eff, "kelly_f": last.kelly_f,
            "sharpe_ann": last.sharpe_ann, "incubating": last.incubating,
        })
    return out


@router.get("/strategies/{name}/history")
def strategy_history(name: str, limit: int = 500, db: Session = Depends(get_db)):
    rows = (db.query(StrategyStat).filter(StrategyStat.strategy == name)
            .order_by(StrategyStat.ts.desc()).limit(min(limit, 5000)).all())
    rows.reverse()
    return [{
        "ts": _iso(r.ts), "mu": r.mu, "var": r.var, "n_eff": r.n_eff,
        "kelly_f": r.kelly_f, "sharpe_ann": r.sharpe_ann,
    } for r in rows]


@router.get("/regime/current")
def regime_current(db: Session = Depends(get_db)):
    r = db.query(RegimeHistoryRow).order_by(RegimeHistoryRow.ts.desc()).first()
    if r is None:
        return {"available": False}
    return {
        "available": True, "ts": _iso(r.ts), "label": r.label, "probs": r.probs,
        "risk_scaler": r.risk_scaler, "strategy_weights": r.strategy_weights,
        "source": r.source,
    }


@router.get("/regime/history")
def regime_history(limit: int = 1000, db: Session = Depends(get_db)):
    rows = (db.query(RegimeHistoryRow).order_by(RegimeHistoryRow.ts.desc())
            .limit(min(limit, 10000)).all())
    rows.reverse()
    return [{
        "ts": _iso(r.ts), "label": r.label, "probs": r.probs,
        "risk_scaler": r.risk_scaler, "source": r.source,
    } for r in rows]


# --------------------------------------------------------------------- risk
@router.get("/risk/limits")
def risk_limits(db: Session = Depends(get_db)):
    """Current limit utilisation, extracted from the latest decision's audit
    trail (cap rules log before/after) + configured limits from runtime_config."""
    mode = _mode(db)
    d = (db.query(DecisionRow).filter(DecisionRow.mode == mode)
         .order_by(DecisionRow.ts.desc()).first())
    rc = _rc_all(db)
    cap_events = []
    if d is not None:
        cap_events = [a for a in (d.audit or [])
                      if a.get("stage", "").startswith("risk")
                      or a.get("stage") in ("sizing", "vol_target", "regime")]
    last_eq = (db.query(EquityPoint).filter(EquityPoint.mode == mode)
               .order_by(EquityPoint.ts.desc()).first())
    return {
        "decision_id": d.id if d else None,
        "decision_ts": _iso(d.ts) if d else None,
        "halted": d.halted if d else False,
        "kill_reason": d.kill_reason if d else None,
        "risk_frac_eff": d.risk_frac_eff if d else None,
        "vol_scaler": d.vol_scaler if d else None,
        "gross_exposure": last_eq.gross_exposure if last_eq else None,
        "net_exposure": last_eq.net_exposure if last_eq else None,
        "equity": last_eq.equity if last_eq else None,
        "config_overrides": {k: v for k, v in rc.items()
                             if k.split(".")[0] in ("sizing", "vol_target", "drawdown",
                                                    "exposure", "engine")},
        "audit_events": cap_events,
    }


@router.get("/risk/events")
def risk_events(limit: int = 100, kind: str | None = None,
                db: Session = Depends(get_db)):
    q = db.query(RiskEvent)
    if kind:
        q = q.filter(RiskEvent.kind == kind)
    rows = q.order_by(RiskEvent.id.desc()).limit(min(limit, 1000)).all()
    return [{
        "id": r.id, "ts": _iso(r.ts), "mode": r.mode, "kind": r.kind,
        "rule": r.rule, "symbol": r.symbol, "severity": r.severity,
        "cause": r.cause, "detail": r.detail, "decision_id": r.decision_id,
        "acknowledged_at": _iso(r.acknowledged_at),
    } for r in rows]


# ------------------------------------------------------------------ capital
@router.get("/capital")
def capital(db: Session = Depends(get_db)):
    mode = _mode(db)
    rc = _rc_all(db)
    last_eq = (db.query(EquityPoint).filter(EquityPoint.mode == mode)
               .order_by(EquityPoint.ts.desc()).first())
    d = (db.query(DecisionRow).filter(DecisionRow.mode == mode)
         .order_by(DecisionRow.ts.desc()).first())
    # tier ladder thresholds come from the engine config snapshot the engine
    # publishes into runtime_config on startup (key: engine.tier_ladder)
    ladder = rc.get("engine.tier_ladder") or []
    return {
        "mode": mode,
        "equity": last_eq.equity if last_eq else None,
        "equity_ts": _iso(last_eq.ts) if last_eq else None,
        "tier": d.tier_name if d else None,
        "paper_capital": rc.get("paper_capital"),
        "deployable_cap_frac": rc.get("deployable_cap_frac"),
        "deployable_cap_abs": rc.get("deployable_cap_abs"),
        "tier_ladder": ladder,
        "hysteresis": rc.get("engine.tier_hysteresis"),
    }


# ------------------------------------------------- backtests / logs / alerts
@router.get("/backtests")
def backtests(limit: int = 50, db: Session = Depends(get_db)):
    rows = (db.query(BacktestRun).order_by(BacktestRun.id.desc())
            .limit(min(limit, 200)).all())
    return [{
        "id": b.id, "created_at": _iso(b.created_at), "label": b.label,
        "git_rev": b.git_rev, "metrics": b.metrics,
    } for b in rows]


@router.get("/backtests/{run_id}")
def backtest_detail(run_id: int, db: Session = Depends(get_db)):
    b = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
    if b is None:
        raise HTTPException(404, "backtest not found")
    return {
        "id": b.id, "created_at": _iso(b.created_at), "label": b.label,
        "git_rev": b.git_rev, "params": b.params, "metrics": b.metrics,
        "equity_curve": b.equity_curve, "artifacts": b.artifacts,
    }


@router.get("/alerts")
def alerts(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(Alert).order_by(Alert.id.desc()).limit(min(limit, 500)).all()
    return [{
        "id": a.id, "ts": _iso(a.ts), "severity": a.severity, "kind": a.kind,
        "title": a.title, "body": a.body, "channels": a.channels,
        "delivered": a.delivered, "delivery_detail": a.delivery_detail,
    } for a in rows]


@router.get("/audit-log")
def audit_log(limit: int = 200, db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)).all()
    return [{
        "id": r.id, "ts": _iso(r.ts), "username": r.username,
        "action": r.action, "detail": r.detail, "ip": r.ip,
    } for r in rows]


@router.get("/config-versions")
def config_versions(limit: int = 200, db: Session = Depends(get_db)):
    rows = (db.query(ConfigVersion).order_by(ConfigVersion.id.desc())
            .limit(min(limit, 1000)).all())
    return [{
        "id": r.id, "ts": _iso(r.ts), "username": r.username, "key": r.key,
        "old_value": r.old_value, "new_value": r.new_value, "reason": r.reason,
    } for r in rows]


@router.get("/downshock")
def downshock_tracker():
    """Forward paper-tracker for the Pillar-4 down-shock lead — a READ-ONLY
    research monitor (trades nothing). Reads the JSON/CSV the VPS daily job
    writes; absent => not available (local/dev), which the UI shows as 'not
    running'. The forward curve is cum-return per run-date (deduped)."""
    base = Path(os.environ.get("DOWNSHOCK_DIR", "/research/downshock"))
    state_f = base / "state.json"
    if not state_f.exists():
        return {"available": False}
    try:
        state = json.loads(state_f.read_text())
    except (ValueError, OSError):
        return {"available": False}
    history: dict[str, dict] = {}
    log_f = base / "state.log.csv"
    if log_f.exists():
        for line in log_f.read_text().splitlines()[1:]:
            p = line.split(",")
            if len(p) < 6:
                continue
            try:
                history[p[0]] = {           # dedupe by run-date (keep last/day)
                    "date": p[0], "forward_events": int(p[2]),
                    "cum_return": float(p[4]),
                    "sharpe": None if p[5] in ("None", "") else float(p[5]),
                }
            except ValueError:
                continue
    return {"available": True, "state": state,
            "history": [history[k] for k in sorted(history)]}


@router.get("/bars/{symbol}")
def bars(symbol: str, tf: int = 5, limit: int = 500, db: Session = Depends(get_db)):
    rows = (db.query(MarketBar)
            .filter(MarketBar.symbol == symbol, MarketBar.tf_minutes == tf)
            .order_by(MarketBar.ts.desc()).limit(min(limit, 5000)).all())
    rows.reverse()
    return [{
        "ts": _iso(b.ts), "open": b.open, "high": b.high,
        "low": b.low, "close": b.close, "volume": b.volume,
    } for b in rows]
