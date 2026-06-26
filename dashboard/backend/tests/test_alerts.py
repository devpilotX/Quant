"""Automated alerting: alert rows persist + publish, delivery is queued (never
inline), and the engine-side helpers raise alerts on trades / risk / lifecycle.
"""

from __future__ import annotations

from qsdash.audit import notify_alert, raise_alert
from qsdash.bus import make_sync_publisher
from qsdash.config import settings
from qsdash.db import SessionLocal
from qsdash.models import Alert


def test_raise_alert_without_creds_persists_no_delivery(db):
    captured: list = []
    a = raise_alert(db, severity="warn", kind="test_kind", title="hello",
                    body="b", publish=lambda t, p: captured.append((t, p)))
    db.commit()
    assert a.id is not None
    assert a.channels == []            # nothing configured -> no channel
    assert a.delivered is False
    assert captured and captured[0][0] == "alerts"  # pushed to live stream
    row = SessionLocal().get(Alert, a.id)
    assert row.title == "hello" and row.kind == "test_kind"


def test_raise_alert_with_creds_enqueues_async(db, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "tok")
    monkeypatch.setattr(settings, "telegram_chat_id", "chat")
    calls: list = []
    # delivery must be queued off-thread, never called inline
    monkeypatch.setattr("qsdash.audit.enqueue_delivery",
                        lambda *a, **k: calls.append((a, k)))
    a = raise_alert(db, severity="crit", kind="kill", title="KILL: dd")
    db.commit()
    assert a.channels == ["telegram"]
    assert calls and calls[0][0][0] == a.id and calls[0][0][2] == "KILL: dd"


def test_notify_alert_self_contained(db):
    pub = make_sync_publisher(SessionLocal)
    notify_alert(pub, severity="info", kind="engine_start", title="started X")
    rows = SessionLocal().query(Alert).filter(Alert.kind == "engine_start").all()
    assert any(r.title == "started X" for r in rows)


def test_paper_broker_emits_trade_alerts(db):
    from qsdash.bridge.paper import PaperBroker

    pub = make_sync_publisher(SessionLocal)
    b = PaperBroker("paper", 1_000_000.0, {}, None, pub)
    b._queue_trade_alert("open", "SBIN-EQ", "trend", 10, 550.0)
    b._queue_trade_alert("close", "SBIN-EQ", "trend", -10, 560.0, realized=100.0)
    assert len(b._pending_trade_alerts) == 2
    b._emit_trade_alerts()
    assert b._pending_trade_alerts == []  # drained

    s = SessionLocal()
    titles = {r.kind: r.title for r in s.query(Alert).all()}
    s.close()
    assert "trade_open" in titles and "LONG 10 SBIN-EQ" in titles["trade_open"]
    assert "trade_close" in titles and "+100" in titles["trade_close"]


def test_feed_alert_delivery_is_rate_limited(monkeypatch):
    """The feed stale/recovered flap raised an alert on every transition and
    spammed Telegram. Repeats of the same non-crit kind now deliver once per
    cooldown; crit alerts (kill/halt) are never throttled."""
    from qsdash import notify

    monkeypatch.setattr(notify.settings, "telegram_bot_token", "tok")
    monkeypatch.setattr(notify.settings, "telegram_chat_id", "chat")
    notify._last_delivery.clear()
    sent: list = []

    class _FakeQ:
        def put(self, item):
            sent.append(item)

    monkeypatch.setattr(notify, "_ensure_worker", lambda: _FakeQ())

    for _ in range(3):  # feed flap (warn): only the first gets delivered
        notify.enqueue_delivery(1, "warn", "feed stale", "", kind="feed_stale")
    assert len(sent) == 1, "non-crit flap must be rate-limited to one delivery"

    for _ in range(3):  # crit is never throttled
        notify.enqueue_delivery(2, "crit", "KILL", "", kind="kill")
    assert len(sent) == 4, "crit alerts (kill/halt) must never be throttled"
