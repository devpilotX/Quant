"""Login (password + mandatory TOTP), logout, session info, re-auth.

Failed-attempt lockout: after ``login_max_failures`` consecutive failures the
account locks for ``login_lockout_minutes``. Every outcome is audit-logged.
The error message never reveals which factor failed.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from qsdash.audit import audit
from qsdash.config import settings
from qsdash.db import now_ist
from qsdash.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    check_ip_allowlist,
    client_ip,
    current_session,
    current_user,
    get_db,
    require_csrf,
)
from qsdash.models import AuthSession, User
from qsdash.security import (
    hash_token,
    new_csrf_token,
    new_session_token,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_GENERIC = "invalid credentials"


class LoginBody(BaseModel):
    username: str
    password: str
    totp: str


class ReauthBody(BaseModel):
    password: str
    totp: str


def _set_cookies(response: Response, raw_token: str, csrf: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, raw_token,
        httponly=True, secure=settings.cookie_secure, samesite="strict",
        max_age=settings.session_absolute_hours * 3600, path="/",
    )
    response.set_cookie(
        CSRF_COOKIE, csrf,
        httponly=False, secure=settings.cookie_secure, samesite="strict",
        max_age=settings.session_absolute_hours * 3600, path="/",
    )


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response,
          db: Session = Depends(get_db)):
    check_ip_allowlist(request)
    ip = client_ip(request)
    user = db.query(User).filter(User.username == body.username).first()
    if user is None:
        audit(db, body.username, "login.failed", {"reason": "unknown user"}, ip)
        db.commit()
        raise HTTPException(401, _GENERIC)

    now = now_ist()
    if user.locked_until is not None and now < user.locked_until:
        audit(db, user.username, "login.locked_out", {}, ip)
        db.commit()
        raise HTTPException(423, "account temporarily locked")

    ok = verify_password(user.password_hash, body.password) and verify_totp(
        user.totp_secret, body.totp
    )
    if not ok:
        user.failed_attempts += 1
        detail = {"failures": user.failed_attempts}
        if user.failed_attempts >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_attempts = 0
            detail["locked_until"] = user.locked_until.isoformat()
        audit(db, user.username, "login.failed", detail, ip)
        db.commit()
        raise HTTPException(401, _GENERIC)

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    raw, token_hash = new_session_token()
    csrf = new_csrf_token()
    db.add(AuthSession(
        token_hash=token_hash, user_id=user.id, ip=ip,
        user_agent=request.headers.get("user-agent", "")[:250],
        csrf_token=csrf,
    ))
    audit(db, user.username, "login.success", {}, ip)
    db.commit()
    _set_cookies(response, raw, csrf)
    return {"ok": True, "username": user.username}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           sess: AuthSession = Depends(require_csrf)):
    sess.revoked = True
    user = db.query(User).filter(User.id == sess.user_id).first()
    audit(db, user.username if user else "?", "logout", {}, client_ip(request))
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(db: Session = Depends(get_db), sess: AuthSession = Depends(current_session)):
    user = db.query(User).filter(User.id == sess.user_id).first()
    fresh_until = None
    if sess.reauth_at is not None:
        fresh_until = (
            sess.reauth_at + timedelta(minutes=settings.reauth_window_minutes)
        ).isoformat()
    return {
        "username": user.username,
        "session_created": sess.created_at.isoformat(),
        "reauth_fresh_until": fresh_until,
    }


@router.post("/reauth")
def reauth(body: ReauthBody, request: Request, db: Session = Depends(get_db),
           sess: AuthSession = Depends(require_csrf)):
    """Refresh the high-risk-action window. Full second factor required."""
    user = db.query(User).filter(User.id == sess.user_id).first()
    ip = client_ip(request)
    now = now_ist()
    if user.locked_until is not None and now < user.locked_until:
        raise HTTPException(423, "account temporarily locked")
    ok = verify_password(user.password_hash, body.password) and verify_totp(
        user.totp_secret, body.totp
    )
    if not ok:
        user.failed_attempts += 1
        if user.failed_attempts >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_attempts = 0
        audit(db, user.username, "reauth.failed", {}, ip)
        db.commit()
        raise HTTPException(401, _GENERIC)
    user.failed_attempts = 0
    sess.reauth_at = now
    audit(db, user.username, "reauth.success", {}, ip)
    db.commit()
    return {
        "ok": True,
        "fresh_until": (now + timedelta(minutes=settings.reauth_window_minutes)).isoformat(),
    }


# imported for type re-export convenience in main.py
__all__ = ["router", "current_user"]
