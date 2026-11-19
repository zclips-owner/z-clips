"""Test fixtures for Z CLIPS.

Every test gets a FRESH temporary SQLite database (never the dev DB) and
a TestClient. The app's `get_db` dependency is overridden to use the test
database. CSRF tokens are fetched from the rendered forms by the helpers.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# The project root must be importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SESSION_SECRET", "test-secret-that-is-long-enough-0123456789")
os.environ.setdefault("OWNER_SETUP_TOKEN", "test-owner-token-that-is-long-enough-0123456789")

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the in-memory rate limiter before every test.

    All TestClient requests share one client IP, so without this the
    login bucket (10/min/IP) would be exhausted by earlier tests.
    Rate-limit behaviour itself is tested explicitly in test_security.py
    and the HTTP 429 path in test_auth.py.
    """
    from app.security import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestingSession
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture()
def client(tmp_db):
    # follow_redirects=False: redirects must be observable as 303 + Location.
    # This Starlette version's TestClient defaults follow_redirects to True,
    # which silently turns every 303 into the 200 of the next page.
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


def csrf_token_from(html: str) -> str | None:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    return m.group(1) if m else None


def make_user(session_factory, username="alice", email="alice@example.com",
              password="correct-horse-battery-staple", role="customer",
              plan="free", credits=5, is_active=True):
    db = session_factory()
    try:
        user = User(
            username=username, email=email,
            password_hash=hash_password(password),
            role=role, plan=plan, credits=credits, is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def make_owner(session_factory, password="owner-password-that-is-long-enough"):
    return make_user(session_factory, username="zclips_owner",
                     email="owner@zclips.local", password=password,
                     role="owner", plan="pro", credits=0)


def login(client, username, password):
    """Log in and return the response (follows the redirect to the dashboard).

    Fetches a fresh CSRF token from the login page first, since POST
    /login validates CSRF like every other POST.
    """
    token = csrf_token_from(client.get("/login").text)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token or ""},
    )


def login_as(client, session_factory, username, password):
    resp = login(client, username, password)
    assert resp.status_code in (200, 303), f"login failed: {resp.status_code}"
    return resp


def get_csrf(client, url: str) -> str:
    """GET a page and extract its CSRF token."""
    resp = client.get(url)
    assert resp.status_code == 200, f"GET {url} -> {resp.status_code}"
    token = csrf_token_from(resp.text)
    assert token, f"no csrf_token found on {url}"
    return token
