"""Audit-log + alert helpers shared by API and bridge."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from qsdash.models import Alert, AuditLog
from qsdash.notify import channels_configured, enqueue_delivery

log = logging.getLogger(__name__)


def audit(db: Session, username: str, action: str, detail: dict | None = None,
          ip: str = "") -> None:
    db.add(AuditLog(username=username, action=action, detail=detail or {}, ip=ip))


def raise_alert(db: Session, *, severity: str, kind: str, title: str,
                body: str = "", publish=None) -> Alert:
    """Persist an alert row, queue delivery, publish to the live stream.

    Delivery happens off-thread (see notify.enqueue_delivery) and writes its
    outcome back onto the row, so a slow channel never blocks the caller — the
    engine raises alerts from its decision/fill loop. An undeliverable alert
    still lands in the DB and the UI.
    """
    chans = channels_configured()
    a = Alert(severity=severity, kind=kind, title=title, body=body,
              channels=chans, delivered=False,
              delivery_detail={"status": "pending"} if chans else {})
    db.add(a)
    db.flush()
    enqueue_delivery(a.id, severity, title, body)
    if publish is not None:
        publish("alerts", {
            "id": a.id, "severity": severity, "kind": kind,
            "title": title, "body": body, "delivered": a.delivered,
        })
    return a


def notify_alert(publisher, *, severity: str, kind: str, title: str,
                 body: str = "") -> None:
    """Best-effort alert from an engine-side caller (recorder, broker, runner).

    Opens its own session, persists + queues delivery + publishes, and SWALLOWS
    every error — raising an alert must never break trade recording or the
    engine loop.
    """
    from qsdash.bus import PgSyncPublisher
    from qsdash.db import SessionLocal

    sess = SessionLocal()
    bound = isinstance(publisher, PgSyncPublisher)
    try:
        if bound:
            publisher.bind(sess)
        raise_alert(sess, severity=severity, kind=kind, title=title, body=body,
                    publish=publisher.publish if publisher else None)
        sess.commit()
    except Exception as e:  # pragma: no cover - defensive
        log.error("notify_alert(%s/%s) failed: %s", kind, title, e)
        try:
            sess.rollback()
        except Exception:
            pass
    finally:
        if bound:
            publisher.bind(None)
        sess.close()
