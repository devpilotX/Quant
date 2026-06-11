"""The financial-safety tests: no path to a live/real-money action without
fresh reauth + typed phrase + explicit cap. These encode the product's
non-negotiables — do not weaken them."""

from __future__ import annotations

from qsdash.config import settings
from qsdash.models import Command, PositionRow, RuntimeConfig
from tests.conftest import PASSWORD


def _reauth(client, totp):
    r = client.post("/api/auth/reauth", json={"password": PASSWORD,
                                              "totp": totp.now()})
    assert r.status_code == 200


def test_mode_switch_requires_fresh_reauth(authed):
    r = authed.post("/api/control/mode", json={
        "target_mode": "live",
        "confirmation_phrase": settings.live_confirmation_phrase,
        "deployable_cap_frac": 0.5,
    })
    assert r.status_code == 403
    assert "re-authentication" in r.json()["detail"]


def test_live_requires_exact_confirmation_phrase(authed, totp):
    _reauth(authed, totp)
    r = authed.post("/api/control/mode", json={
        "target_mode": "live",
        "confirmation_phrase": "go live real money",  # wrong case = wrong
        "deployable_cap_frac": 0.5,
    })
    assert r.status_code == 400
    assert "confirmation phrase" in r.json()["detail"]


def test_live_requires_deployable_cap(authed, totp):
    _reauth(authed, totp)
    r = authed.post("/api/control/mode", json={
        "target_mode": "live",
        "confirmation_phrase": settings.live_confirmation_phrase,
    })
    assert r.status_code == 400
    assert "deployable-capital cap" in r.json()["detail"]


def test_live_blocked_by_open_paper_positions(authed, totp, db):
    from qsdash.db import now_ist

    db.add(PositionRow(mode="paper", symbol="TEST", qty=10, avg_price=100,
                       status="open", opened_at=now_ist()))
    db.commit()
    _reauth(authed, totp)
    r = authed.post("/api/control/mode", json={
        "target_mode": "live",
        "confirmation_phrase": settings.live_confirmation_phrase,
        "deployable_cap_frac": 0.5,
    })
    assert r.status_code == 409
    assert "paper positions are open" in r.json()["detail"]
    db.query(PositionRow).delete()
    db.commit()


def test_live_request_queues_command_not_direct_flip(authed, totp, db):
    _reauth(authed, totp)
    r = authed.post("/api/control/mode", json={
        "target_mode": "live",
        "confirmation_phrase": settings.live_confirmation_phrase,
        "deployable_cap_frac": 0.25,
        "reason": "test",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    cmd = db.get(Command, body["command_id"])
    assert cmd is not None and cmd.kind == "set_mode" and cmd.status == "pending"
    # CRITICAL: mode itself did NOT flip — only the engine may do that
    mode = db.query(RuntimeConfig).filter(RuntimeConfig.key == "mode").first()
    assert mode.value["v"] == "paper"
    requested = db.query(RuntimeConfig).filter(
        RuntimeConfig.key == "mode_requested").first()
    assert requested.value["v"] == "live"


def test_paper_capital_bounds(authed, totp):
    _reauth(authed, totp)
    assert authed.post("/api/control/paper-capital",
                       json={"capital": 1}).status_code == 422
    assert authed.post("/api/control/paper-capital",
                       json={"capital": 10_000_000_000}).status_code == 422
    r = authed.post("/api/control/paper-capital", json={"capital": 5_000_000})
    assert r.status_code == 200


def test_config_keys_allowlisted(authed, totp):
    _reauth(authed, totp)
    r = authed.post("/api/control/config", json={
        "key": "engine.secret_backdoor", "value": 1,
    })
    assert r.status_code == 400
    r = authed.post("/api/control/config", json={
        "key": "sizing.base_risk_frac", "value": 0.004,
    })
    assert r.status_code == 200


def test_kill_requires_reauth(authed):
    r = authed.post("/api/control/kill", json={"action": "kill"})
    assert r.status_code == 403
