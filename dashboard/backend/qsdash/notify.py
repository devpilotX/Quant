"""Outbound notification channels. Telegram first; the dict-of-results shape
leaves room for email/Slack later without touching callers.

Delivery is OFF the caller's thread: ``enqueue_delivery`` hands the alert to a
daemon worker that posts to Telegram and writes the outcome back onto the alert
row. The engine raises alerts from its hot path (trade fills, risk events), so
delivery must never block that loop on a slow/timing-out HTTP call.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

import httpx

from qsdash.config import settings

log = logging.getLogger(__name__)

_SEV_ICON = {"info": "i", "warn": "!", "crit": "!!"}


def channels_configured() -> list[str]:
    """Which delivery channels have credentials right now."""
    out = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        out.append("telegram")
    return out


def deliver_alert(*, severity: str, title: str, body: str = "") -> dict:
    """Synchronous delivery to every configured channel. Returns per-channel
    results. Kept for callers that want inline delivery; engine/API callers use
    the async ``enqueue_delivery`` path via ``raise_alert``."""
    results: dict[str, dict] = {}
    if "telegram" in channels_configured():
        results["telegram"] = _telegram(severity, title, body)
    return results


def _telegram(severity: str, title: str, body: str) -> dict:
    text = f"[{_SEV_ICON.get(severity, 'i')}] quantsys: {title}"
    if body:
        text += f"\n{body}"
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10,
        )
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as e:
        log.error("telegram delivery failed: %s", e)
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------- async delivery
_Q: "queue.Queue[tuple[int, str, str, str]] | None" = None
_worker_lock = threading.Lock()


def _ensure_worker() -> "queue.Queue":
    global _Q
    with _worker_lock:
        if _Q is None:
            _Q = queue.Queue()
            threading.Thread(target=_delivery_worker, daemon=True,
                             name="alert-delivery").start()
        return _Q


def enqueue_delivery(alert_id: int, severity: str, title: str, body: str) -> None:
    """Queue an alert for off-thread delivery. No-op if no channel is
    configured (nothing to deliver), so the worker only runs when it can."""
    if not channels_configured():
        return
    _ensure_worker().put((alert_id, severity, title, body))


def _delivery_worker() -> None:  # pragma: no cover - exercised via integration
    from qsdash.db import SessionLocal
    from qsdash.models import Alert

    assert _Q is not None
    while True:
        alert_id, severity, title, body = _Q.get()
        try:
            results = deliver_alert(severity=severity, title=title, body=body)
        except Exception as e:  # defensive; deliver_alert already guards
            results = {"telegram": {"ok": False, "error": str(e)}}
        delivered = any(v.get("ok") for v in results.values()) if results else False
        # write the outcome back; retry briefly to absorb the caller's commit
        for attempt in range(15):
            sess = SessionLocal()
            try:
                a = sess.get(Alert, alert_id)
                if a is not None:
                    a.delivered = delivered
                    a.delivery_detail = results
                    a.channels = list(results.keys())
                    sess.commit()
                    break
            except Exception as e:
                log.error("alert %s delivery write failed: %s", alert_id, e)
                sess.rollback()
                break
            finally:
                sess.close()
            time.sleep(0.2)  # row not committed yet — wait and retry
