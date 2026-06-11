"""Outbound notification channels. Telegram first; the dict-of-results shape
leaves room for email/Slack later without touching callers."""

from __future__ import annotations

import logging

import httpx

from qsdash.config import settings

log = logging.getLogger(__name__)

_SEV_ICON = {"info": "i", "warn": "!", "crit": "!!"}


def deliver_alert(*, severity: str, title: str, body: str = "") -> dict:
    results: dict[str, dict] = {}
    if settings.telegram_bot_token and settings.telegram_chat_id:
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
