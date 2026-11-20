"""One-time upload tokens for direct-to-backend video uploads.

Why this exists: the PWA runs on Vercel (Hobby), whose serverless proxy
rejects request bodies over ~4.5 MB, so a video file cannot be POSTed
through the ``/api/v1/*`` proxy. Instead the PWA first asks the backend
(via the small, proxied ``POST /api/v1/clips/upload-token``) for a
one-time token, then POSTs the video file DIRECTLY to the backend's
``POST /api/v1/clips/direct-upload`` with that token as a form field.

The session cookie CANNOT be used for the direct upload: it is set for
the Vercel domain (via the proxy), so the browser will not send it on a
cross-origin POST to the backend origin. The upload token IS the auth
for that one request — that is why it is unguessable, short-lived
(15 minutes) and single-use.

Storage is a small in-memory dict. Limitation, stated honestly: tokens
do NOT survive a backend restart — a token issued before a restart is
rejected with 401 and the PWA simply asks for a fresh one. That is safe
(it fails closed) and the token flow is cheap to redo. The web process
is the only process that issues or consumes tokens, so no cross-process
sharing is needed.
"""
from __future__ import annotations

import secrets
import time

TOKEN_TTL_SECONDS = 15 * 60  # 15 minutes

# token -> (user_id, expires_at_monotonic)
_store: dict[str, tuple[int, float]] = {}


def _purge_expired(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [t for t, (_, exp) in _store.items() if exp <= now]
    for t in expired:
        _store.pop(t, None)


def issue_upload_token(user_id: int) -> str:
    """Create a one-time upload token for ``user_id`` and return it."""
    _purge_expired()
    token = secrets.token_urlsafe(32)
    _store[token] = (user_id, time.monotonic() + TOKEN_TTL_SECONDS)
    return token


def consume_upload_token(token: str | None) -> int | None:
    """Validate and consume a token. Returns the user_id, or None when
    the token is missing, unknown, expired, or already used.

    Consumption happens on first presentation (the entry is popped
    before the expiry check), so a token can never be replayed — not
    even an expired one.
    """
    if not token or not isinstance(token, str):
        return None
    entry = _store.pop(token, None)
    if entry is None:
        return None
    user_id, expires_at = entry
    if time.monotonic() > expires_at:
        return None
    return user_id


def _clear_for_tests() -> None:
    """Test helper: drop every stored token."""
    _store.clear()
