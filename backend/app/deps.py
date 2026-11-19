"""Shared request dependencies: authentication, authorization, CSRF and
rate limiting. Every protected route uses these — never trust the browser
for role, plan or ownership checks.
"""
from fastapi import Request, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from .config import SESSION_SECRET, TRUST_PROXY
from .database import get_db
from .models import User
from .security import (
    ensure_session_id,
    new_csrf_token,
    validate_csrf_token,
    rate_limit_ok,
    client_ip,
)


def current_user(request: Request, db: Session) -> User | None:
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = db.get(User, uid)
    if not user or not user.is_active or user.is_suspended:
        request.session.clear()
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_owner(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required.")
    return user


def require_pro(request: Request, db: Session = Depends(get_db)) -> User:
    """Require a user whose entitlements include YouTube Live monitoring
    (Pro, owner or complimentary premium)."""
    from .plans import entitlements_for

    user = require_user(request, db)
    if not entitlements_for(user)["youtube_live_monitoring"]:
        raise HTTPException(status_code=403, detail="YouTube Live monitoring requires the Pro plan.")
    return user


def csrf_token_for(request: Request) -> str:
    sid = ensure_session_id(request.session)
    return new_csrf_token(SESSION_SECRET, sid)


def validate_csrf(request: Request, token: str | None) -> None:
    sid = request.session.get("sid") or ""
    if not validate_csrf_token(SESSION_SECRET, sid, token):
        raise HTTPException(status_code=403, detail="Invalid or expired form token. Please reload the page and try again.")


def check_rate_limit(request: Request, bucket: str, key: str | None = None) -> None:
    """Raise 429 when the bucket is exhausted. ``key`` defaults to the
    client IP; pass a user id for per-user buckets."""
    k = key or client_ip(request) if TRUST_PROXY else (key or (request.client.host if request.client else "unknown"))
    if not rate_limit_ok(bucket, k):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")


def base_context(request: Request, user: User | None = None) -> dict:
    """Context every template receives: the signed-in user (if any),
    a fresh CSRF token, and the owner flag."""
    return {
        "request": request,
        "current_user": user,
        "is_owner": bool(user and user.role == "owner"),
        "csrf_token": csrf_token_for(request),
    }


def redirect_login():
    return RedirectResponse("/login", status_code=303)
