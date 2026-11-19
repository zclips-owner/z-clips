"""Unit tests for CSRF tokens and the rate limiter."""
from app.security import (
    new_csrf_token,
    validate_csrf_token,
    RateLimiter,
)

SECRET = "test-secret-that-is-long-enough-0123456789"


def test_csrf_token_validates_for_same_session():
    token = new_csrf_token(SECRET, "session-abc")
    assert validate_csrf_token(SECRET, "session-abc", token) is True


def test_csrf_token_rejected_for_other_session():
    token = new_csrf_token(SECRET, "session-abc")
    assert validate_csrf_token(SECRET, "session-xyz", token) is False


def test_csrf_token_rejected_for_other_secret():
    token = new_csrf_token(SECRET, "session-abc")
    assert validate_csrf_token("a-different-secret-0123456789", "session-abc", token) is False


def test_csrf_token_rejected_when_missing_or_garbage():
    assert validate_csrf_token(SECRET, "session-abc", None) is False
    assert validate_csrf_token(SECRET, "session-abc", "") is False
    assert validate_csrf_token(SECRET, "session-abc", "not-a-token") is False


def test_rate_limiter_allows_up_to_limit():
    limiter = RateLimiter()
    for _ in range(5):
        assert limiter.allow("k", 5, 60, now=1000.0) is True
    assert limiter.allow("k", 5, 60, now=1000.0) is False


def test_rate_limiter_window_slides():
    limiter = RateLimiter()
    for _ in range(3):
        assert limiter.allow("k", 3, 10, now=1000.0) is True
    assert limiter.allow("k", 3, 10, now=1005.0) is False
    # after the window passes, requests are allowed again
    assert limiter.allow("k", 3, 10, now=1011.0) is True


def test_rate_limiter_keys_are_independent():
    limiter = RateLimiter()
    assert limiter.allow("a", 1, 60, now=1000.0) is True
    assert limiter.allow("b", 1, 60, now=1000.0) is True
    assert limiter.allow("a", 1, 60, now=1000.0) is False
    assert limiter.allow("b", 1, 60, now=1000.0) is False
