"""Backend tests run on SQLite (file-per-session) so they need no live
Postgres; the NOTIFY publisher no-ops off-postgres by design. Environment is
pinned BEFORE any qsdash import."""

from __future__ import annotations

import os
from pathlib import Path

_TMP = Path(__file__).resolve().parent / "_tmp"
_TMP.mkdir(exist_ok=True)
_DB = _TMP / "test.db"
if _DB.exists():
    _DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["COOKIE_SECURE"] = "false"
os.environ["ANGEL_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["ENV"] = "dev"
os.environ.pop("REDIS_URL", None)

# repo root on sys.path for `config/base.yaml` resolution in the e2e test
REPO_ROOT = Path(__file__).resolve().parents[3]

import pyotp  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from qsdash.db import Base, engine, SessionLocal  # noqa: E402
from qsdash.main import app  # noqa: E402
from qsdash.models import RuntimeConfig, User  # noqa: E402
from qsdash.security import hash_password, new_totp_secret  # noqa: E402

USERNAME = "op"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    db.add(User(username=USERNAME, password_hash=hash_password(PASSWORD),
                totp_secret=new_totp_secret()))
    db.add(RuntimeConfig(key="mode", value={"v": "paper"}, updated_by="test"))
    db.commit()
    db.close()
    yield


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def totp(db):
    user = db.query(User).filter(User.username == USERNAME).first()
    return pyotp.TOTP(user.totp_secret)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def authed(client, totp, db):
    """Logged-in client + csrf header helper."""
    r = client.post("/api/auth/login", json={
        "username": USERNAME, "password": PASSWORD, "totp": totp.now(),
    })
    assert r.status_code == 200, r.text
    csrf = client.cookies.get("qs_csrf")
    client.headers.update({"X-CSRF-Token": csrf})
    # reset throttle state between tests
    user = db.query(User).filter(User.username == USERNAME).first()
    user.failed_attempts = 0
    user.locked_until = None
    db.commit()
    return client
