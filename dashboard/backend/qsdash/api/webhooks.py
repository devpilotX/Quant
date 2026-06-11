"""Inbound webhooks. Angel One order postback:

- authenticity: HMAC-SHA256 of the raw body with ANGEL_WEBHOOK_SECRET in the
  ``X-QS-Signature`` header (hex). Angel One's native postback doesn't sign,
  so on the VPS nginx exposes this endpoint only on a secret path and the
  engine's postback registration includes that path + the relay adds the HMAC.
  Defense: signature required whenever a secret is configured.
- idempotency: (source, external_id) unique in webhook_events; duplicates are
  acknowledged but not re-processed.
- effect: order status/fill update persisted, reconciled against our order
  by broker_order_id / client order id, event published.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from qsdash.bus import make_sync_publisher
from qsdash.config import settings
from qsdash.db import SessionLocal, now_ist
from qsdash.models import FillRow, OrderRow, WebhookEvent

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_publisher = make_sync_publisher(SessionLocal)

# Angel One postback status vocabulary -> internal lifecycle
_STATUS_MAP = {
    "open": "SUBMITTED",
    "pending": "SUBMITTED",
    "trigger pending": "SUBMITTED",
    "complete": "FILLED",
    "executed": "FILLED",
    "partially executed": "PARTIAL",
    "cancelled": "CANCELLED",
    "rejected": "REJECTED",
}


def _verify_signature(raw: bytes, signature: str) -> bool:
    if not settings.angel_webhook_secret:
        # dev convenience only; prod MUST set the secret (deploy checklist)
        return True
    expected = hmac.new(
        settings.angel_webhook_secret.encode(), raw, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@router.post("/angelone")
async def angelone_postback(request: Request):
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("X-QS-Signature", "")):
        raise HTTPException(401, "bad signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON")

    ext_id = str(
        payload.get("orderid")
        or payload.get("norenordno")
        or payload.get("uniqueorderid")
        or ""
    )
    if not ext_id:
        raise HTTPException(400, "no order id in payload")
    # status changes share an order id; key on (id, status, fill qty) so each
    # lifecycle step processes exactly once
    dedup_key = f"{ext_id}:{payload.get('status', '')}:{payload.get('filledshares', '')}"

    db = SessionLocal()
    try:
        ev = WebhookEvent(source="angelone", external_id=dedup_key, payload=payload)
        db.add(ev)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"ok": True, "duplicate": True}

        order = (
            db.query(OrderRow)
            .filter(
                (OrderRow.broker_order_id == ext_id)
                | (OrderRow.client_order_id == str(payload.get("ordertag", "")))
            )
            .first()
        )
        raw_status = str(payload.get("status", "")).lower()
        mapped = _STATUS_MAP.get(raw_status)

        if order is None:
            # An order we don't know is a reconciliation red flag, not noise.
            ev.status = "ignored"
            ev.error = "no matching internal order"
            db.commit()
            _publisher.publish("risk", {
                "kind": "unknown_broker_order", "broker_order_id": ext_id,
                "status": raw_status,
            })
            return {"ok": True, "matched": False}

        order.broker_order_id = ext_id
        hist = list(order.status_history or [])
        hist.append({"ts": now_ist().isoformat(), "status": raw_status, "via": "postback"})
        order.status_history = hist
        order.updated_at = now_ist()
        if mapped:
            order.status = mapped
        if mapped == "REJECTED":
            order.reject_reason = str(payload.get("text", "") or payload.get("message", ""))

        filled = int(float(payload.get("filledshares", 0) or 0))
        avg_px = float(payload.get("averageprice", 0) or 0)
        new_qty = filled - order.filled_qty
        if new_qty > 0 and avg_px > 0:
            signed = new_qty if order.side == "BUY" else -new_qty
            db.add(FillRow(
                order_id=order.id, client_order_id=order.client_order_id,
                ts=now_ist(), mode=order.mode, symbol=order.symbol,
                strategy=order.strategy, qty=signed, price=avg_px,
                slippage=(avg_px - order.ref_price) * (1 if order.side == "BUY" else -1)
                if order.ref_price else 0.0,
            ))
            order.filled_qty = filled

        ev.status = "processed"
        ev.processed_at = now_ist()
        db.commit()
        _publisher.publish("orders", {
            "id": order.id, "client_order_id": order.client_order_id,
            "symbol": order.symbol, "status": order.status,
            "filled_qty": order.filled_qty, "via": "postback",
        })
        return {"ok": True, "matched": True, "status": order.status}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        log.exception("postback processing failed")
        # persist the failure for forensics; never silently drop
        try:
            db.add(WebhookEvent(source="angelone", external_id=f"err:{dedup_key}:{now_ist().timestamp()}",
                                payload=payload, status="error", error=str(e)))
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(500, "processing error")
    finally:
        db.close()
