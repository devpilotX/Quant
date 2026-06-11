"""Auth: TOTP mandatory, lockout, session cookies, CSRF, reauth freshness."""

from __future__ import annotations

from qsdash.config import settings
from qsdash.models import User
from tests.conftest import PASSWORD, USERNAME


def _reset(db):
    u = db.query(User).filter(User.username == USERNAME).first()
    u.failed_attempts = 0
    u.locked_until = None
    db.commit()


def test_login_requires_valid_totp(client, db):
    _reset(db)
    r = client.post("/api/auth/login", json={
        "username": USERNAME, "password": PASSWORD, "totp": "000000",
    })
    assert r.status_code == 401
    _reset(db)


def test_login_wrong_password_generic_error(client, totp, db):
    _reset(db)
    r = client.post("/api/auth/login", json={
        "username": USERNAME, "password": "wrong", "totp": totp.now(),
    })
    assert r.status_code == 401
    assert "invalid credentials" in r.text
    _reset(db)


def test_lockout_after_max_failures(client, totp, db):
    _reset(db)
    for _ in range(settings.login_max_failures):
        client.post("/api/auth/login", json={
            "username": USERNAME, "password": "wrong", "totp": "000000",
        })
    r = client.post("/api/auth/login", json={
        "username": USERNAME, "password": PASSWORD, "totp": totp.now(),
    })
    assert r.status_code == 423  # locked even with correct credentials
    _reset(db)


def test_protected_endpoint_requires_session(client):
    assert client.get("/api/overview").status_code == 401


def test_login_grants_access_and_me(authed):
    assert authed.get("/api/overview").status_code == 200
    me = authed.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == USERNAME
    assert me.json()["reauth_fresh_until"] is None  # no reauth yet


def test_mutations_require_csrf_header(authed):
    headers = {"X-CSRF-Token": ""}
    r = authed.post("/api/auth/logout", headers=headers)
    assert r.status_code == 403


def test_reauth_freshens_window(authed, totp):
    r = authed.post("/api/auth/reauth", json={
        "password": PASSWORD, "totp": totp.now(),
    })
    assert r.status_code == 200
    me = authed.get("/api/auth/me").json()
    assert me["reauth_fresh_until"] is not None
