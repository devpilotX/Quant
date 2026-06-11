"""Password hashing (argon2id), TOTP, and session-token primitives."""

from __future__ import annotations

import hashlib
import secrets

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()  # argon2id defaults (time_cost=3, 64MiB) — fine for 1 user


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=username, issuer_name="quantsys @ quant.devpilotx.com"
    )


def verify_totp(secret: str, code: str) -> bool:
    if not code or not code.strip().isdigit():
        return False
    # valid_window=1 tolerates one 30s step of clock skew either side
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def new_session_token() -> tuple[str, str]:
    """Returns (raw_token_for_cookie, sha256_hash_for_db)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)
