"""Angel One postback: signature, idempotency, fill ingestion."""

from __future__ import annotations

import hashlib
import hmac
import json

from qsdash.db import now_ist
from qsdash.models import FillRow, OrderRow, WebhookEvent

SECRET = "test-webhook-secret"


def _signed(client, payload: dict):
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/angelone", content=raw,
                       headers={"X-QS-Signature": sig,
                                "Content-Type": "application/json"})


def test_bad_signature_rejected(client):
    r = client.post("/api/webhooks/angelone", json={"orderid": "X1"},
                    headers={"X-QS-Signature": "deadbeef"})
    assert r.status_code == 401


def test_unknown_order_recorded_and_flagged(client, db):
    r = _signed(client, {"orderid": "UNKNOWN-1", "status": "complete",
                         "filledshares": 10, "averageprice": 101.5})
    assert r.status_code == 200
    assert r.json()["matched"] is False
    ev = (db.query(WebhookEvent)
          .filter(WebhookEvent.external_id.like("UNKNOWN-1%")).first())
    assert ev is not None and ev.status == "ignored"


def test_postback_updates_order_and_creates_fill(client, db):
    o = OrderRow(client_order_id="QS-T1", ts=now_ist(), mode="live",
                 symbol="NIFTY-FUT", side="BUY", qty=65, ref_price=100.0,
                 status="SUBMITTED", broker_order_id="BRK-77")
    db.add(o)
    db.commit()
    r = _signed(client, {"orderid": "BRK-77", "status": "complete",
                         "filledshares": 65, "averageprice": 100.4})
    assert r.status_code == 200 and r.json()["matched"] is True
    db.refresh(o)
    assert o.status == "FILLED" and o.filled_qty == 65
    fill = db.query(FillRow).filter(FillRow.order_id == o.id).first()
    assert fill is not None and fill.qty == 65 and fill.price == 100.4
    assert abs(fill.slippage - 0.4) < 1e-9


def test_duplicate_postback_is_idempotent(client, db):
    payload = {"orderid": "BRK-77", "status": "complete",
               "filledshares": 65, "averageprice": 100.4}
    r = _signed(client, payload)
    assert r.json().get("duplicate") is True
    fills = (db.query(FillRow).join(OrderRow, FillRow.order_id == OrderRow.id)
             .filter(OrderRow.broker_order_id == "BRK-77").all())
    assert len(fills) == 1  # second delivery did not double-book
