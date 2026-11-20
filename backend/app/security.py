"""Password hashing, CSRF protection and rate limiting.

CSRF: a per-session token is signed with itsdangerous (keyed by the session
secret). Every state-changing POST must carry the token as a form field
named ``csrf_token``. GET requests are never state-changing.

Rate limiting: an in-memory sliding-window limiter (single process). For a
multi-worker production deployment this must be replaced with a shared
store (e.g. Redis); see docs/SECURITY.md.
"""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

_hasher = PasswordHasher()

# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


# ---------------------------------------------------------------- CSRF
def _csrf_serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="zclips-csrf")


def new_csrf_token(secret: str, session_id: str) -> str:
    """Create a CSRF token bound to this session. ``session_id`` is a random
    per-session value stored in the session cookie data."""
    raw = secrets.token_urlsafe(24)
    payload = f"{session_id}:{raw}"
    return _csrf_serializer(secret).dumps(payload)


def validate_csrf_token(secret: str, session_id: str, token: str | None, max_age: int = 60 * 60 * 12) -> bool:
    if not token or not session_id:
        return False
    try:
        payload = _csrf_serializer(secret).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return False
    try:
        token_session_id, _raw = payload.split(":", 1)
    except ValueError:
        return False
    return hmac.compare_digest(token_session_id, session_id)


def ensure_session_id(session: dict) -> str:
    """Return the per-session id, creating it on first use."""
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_urlsafe(24)
        session["sid"] = sid
    return sid


# ---------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding-window limiter. ``allow(key)`` returns True when the request
    is within the limit. Limits are (max_requests, window_seconds)."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = None  # single-process; the GIL guards deque ops

    def allow(self, key: str, max_requests: int, window_seconds: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        q = self._hits[key]
        cutoff = now - window_seconds
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= max_requests:
            return False
        q.append(now)
        return True

    def reset(self, key: str | None = None):
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


# Module-level limiter shared by the app (single process).
limiter = RateLimiter()

# Pre-registered buckets: (max_requests, window_seconds)
LIMITS = {
    "login": (10, 60),          # 10 login attempts / minute / IP
    "signup": (5, 60 * 10),     # 5 signups / 10 min / IP
    "owner_setup": (5, 60 * 10),
    "payment_submit": (5, 60 * 10),
    "clip_create": (10, 60),    # 10 clip jobs / minute / user
    "upload_token": (10, 60),   # 10 one-time upload tokens / minute / user
    "generic_post": (60, 60),   # 60 POSTs / minute / user
}


def rate_limit_ok(bucket: str, key: str) -> bool:
    max_requests, window = LIMITS[bucket]
    return limiter.allow(f"{bucket}:{key}", max_requests, window)


def client_ip(request) -> str:
    """Best-effort client IP (respects X-Forwarded-For only when the app is
    behind a trusted reverse proxy — see config TRUST_PROXY)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
