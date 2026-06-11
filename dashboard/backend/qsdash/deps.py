"""FastAPI dependencies: DB session, authentication, re-auth freshness, CSRF.

Security model (single operator, real money):
- Session cookie: httpOnly, Secure, SameSite=Strict, sliding idle timeout +
  absolute lifetime.
- CSRF: double-submit — a non-httpOnly csrf cookie must be echoed in the
  ``X-CSRF-Token`` header on every mutating request (SameSite=Strict already
  blocks classic CSRF; this is defence in depth).
- High-risk actions additionally require a *fresh* re-authentication
  (password + TOTP within ``reauth_window_minutes``).
- Optional IP allowlist applied before anything else.
"""

from __future__ import annotations

import ipaddress
from datetime import timedelta
from typing import Generator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from qsdash.config import settings
from qsdash.db import SessionLocal, now_ist
from qsdash.models import AuthSession, User
from qsdash.security import hash_token

SESSION_COOKIE = "qs_session"
CSRF_COOKIE = "qs_csrf"
CSRF_HEADER = "X-CSRF-Token"


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def check_ip_allowlist(request: Request) -> None:
    if not settings.ip_allowlist.strip():
        return
    ip = client_ip(request)
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(403, "forbidden")
    for entry in settings.ip_allowlist.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return
        except ValueError:
            continue
    raise HTTPException(403, "forbidden")


def _load_session(request: Request, db: Session) -> tuple[AuthSession, User]:
    check_ip_allowlist(request)
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(401, "not authenticated")
    sess = (
        db.query(AuthSession)
        .filter(AuthSession.token_hash == hash_token(raw), AuthSession.revoked == False)  # noqa: E712
        .first()
    )
    if sess is None:
        raise HTTPException(401, "invalid session")
    now = now_ist()
    if now - sess.last_seen_at > timedelta(minutes=settings.session_idle_minutes):
        sess.revoked = True
        db.commit()
        raise HTTPException(401, "session expired (idle)")
    if now - sess.created_at > timedelta(hours=settings.session_absolute_hours):
        sess.revoked = True
        db.commit()
        raise HTTPException(401, "session expired")
    user = db.query(User).filter(User.id == sess.user_id).first()
    if user is None:
        raise HTTPException(401, "invalid session")
    sess.last_seen_at = now
    db.commit()
    return sess, user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    _, user = _load_session(request, db)
    return user


def current_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    sess, _ = _load_session(request, db)
    return sess


def require_csrf(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    """For every mutating endpoint."""
    sess, _ = _load_session(request, db)
    header = request.headers.get(CSRF_HEADER, "")
    if not header or header != sess.csrf_token:
        raise HTTPException(403, "CSRF token missing or invalid")
    return sess


def require_fresh_reauth(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    """For high-risk control actions: mode switch, capital, kill, config."""
    sess = require_csrf(request, db)
    if sess.reauth_at is None or (
        now_ist() - sess.reauth_at > timedelta(minutes=settings.reauth_window_minutes)
    ):
        raise HTTPException(
            403, f"re-authentication required (within {settings.reauth_window_minutes} min)"
        )
    return sess
