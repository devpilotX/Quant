"""Audit-log + alert helpers shared by API and bridge."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from qsdash.models import Alert, AuditLog
from qsdash.notify import deliver_alert

log = logging.getLogger(__name__)


def audit(db: Session, username: str, action: str, detail: dict | None = None,
          ip: str = "") -> None:
    db.add(AuditLog(username=username, action=action, detail=detail or {}, ip=ip))


def raise_alert(db: Session, *, severity: str, kind: str, title: str,
                body: str = "", publish=None) -> Alert:
    """Persist an alert row, attempt delivery, record the outcome.

    Delivery failure never raises — an undeliverable alert must still land in
    the DB and the UI.
    """
    a = Alert(severity=severity, kind=kind, title=title, body=body)
    db.add(a)
    db.flush()
    try:
        result = deliver_alert(severity=severity, title=title, body=body)
        a.channels = list(result.keys())
        a.delivered = any(v.get("ok") for v in result.values()) if result else False
        a.delivery_detail = result
    except Exception as e:  # pragma: no cover - defensive
        a.delivered = False
        a.delivery_detail = {"error": str(e)}
        log.error("alert delivery crashed: %s", e)
    if publish is not None:
        publish("alerts", {
            "id": a.id, "severity": severity, "kind": kind,
            "title": title, "body": body, "delivered": a.delivered,
        })
    return a
