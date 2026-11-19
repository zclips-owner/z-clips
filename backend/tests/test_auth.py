"""Integration tests: signup / login / logout / owner bootstrap / access control."""
import os

from app.config import OWNER_SETUP_TOKEN
from app.models import User
from conftest import get_csrf, login_as, make_owner, make_user

PASSWORD = "correct-horse-battery-staple"
OWNER_PASSWORD = "owner-password-that-is-long-enough"


def _user_by_name(session_factory, username):
    db = session_factory()
    try:
        return db.query(User).filter(User.username == username).one()
    finally:
        db.close()


def test_signup_success_creates_customer_with_free_plan_and_5_credits(client, tmp_db):
    token = get_csrf(client, "/signup")
    resp = client.post(
        "/signup",
        data={"username": "newuser", "email": "newuser@example.com",
              "password": PASSWORD, "csrf_token": token},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    user = _user_by_name(tmp_db, "newuser")
    assert user.role == "customer"
    assert user.plan == "free"
    assert user.credits == 5


def test_signup_ignores_smuggled_role_plan_credits(client, tmp_db):
    token = get_csrf(client, "/signup")
    resp = client.post(
        "/signup",
        data={"username": "sneaky", "email": "sneaky@example.com",
              "password": PASSWORD, "csrf_token": token,
              "role": "owner", "plan": "pro", "credits": 70},
    )
    assert resp.status_code == 303
    user = _user_by_name(tmp_db, "sneaky")
    assert user.role == "customer"
    assert user.plan == "free"
    assert user.credits == 5


def test_signup_rejects_short_username(client, tmp_db):
    token = get_csrf(client, "/signup")
    resp = client.post(
        "/signup",
        data={"username": "ab", "email": "ab@example.com",
              "password": PASSWORD, "csrf_token": token},
    )
    assert resp.status_code == 400
    assert "Username" in resp.text


def test_signup_rejects_short_password(client, tmp_db):
    token = get_csrf(client, "/signup")
    resp = client.post(
        "/signup",
        data={"username": "shortpw", "email": "shortpw@example.com",
              "password": "short123", "csrf_token": token},
    )
    assert resp.status_code == 400
    assert "12 characters" in resp.text


def test_signup_rejects_duplicate_username(client, tmp_db):
    make_user(tmp_db, username="alice", email="alice@example.com")
    token = get_csrf(client, "/signup")
    resp = client.post(
        "/signup",
        data={"username": "alice", "email": "other@example.com",
              "password": PASSWORD, "csrf_token": token},
    )
    assert resp.status_code == 400
    assert "pehle se registered" in resp.text


def test_signup_rejects_duplicate_email(client, tmp_db):
    make_user(tmp_db, username="alice", email="alice@example.com")
    token = get_csrf(client, "/signup")
    resp = client.post(
        "/signup",
        data={"username": "bob123", "email": "alice@example.com",
              "password": PASSWORD, "csrf_token": token},
    )
    assert resp.status_code == 400
    assert "pehle se registered" in resp.text


def test_login_success_redirects_to_dashboard(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    resp = login_as(client, tmp_db, "alice", PASSWORD)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    assert client.get("/dashboard").status_code == 200


def test_login_wrong_password_returns_400(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    token = get_csrf(client, "/login")
    resp = client.post(
        "/login",
        data={"username": "alice", "password": "wrong-password-xyz", "csrf_token": token},
    )
    assert resp.status_code == 400
    assert "Username ya password sahi nahi hai" in resp.text


def test_login_without_csrf_returns_403(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    resp = client.post(
        "/login", data={"username": "alice", "password": PASSWORD},
    )
    assert resp.status_code == 403


def test_login_with_wrong_csrf_returns_403(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    resp = client.post(
        "/login",
        data={"username": "alice", "password": PASSWORD, "csrf_token": "bogus-token"},
    )
    assert resp.status_code == 403


def test_eleventh_rapid_failed_login_returns_429(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    token = get_csrf(client, "/login")
    statuses = []
    for _ in range(11):
        resp = client.post(
            "/login",
            data={"username": "alice", "password": "wrong-password-xyz",
                  "csrf_token": token},
        )
        statuses.append(resp.status_code)
    assert statuses[:10] == [400] * 10
    assert statuses[10] == 429


def test_logout_clears_session(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    assert client.get("/dashboard").status_code == 200
    resp = client.get("/logout")
    assert resp.status_code == 303
    after = client.get("/dashboard")
    assert after.status_code == 303
    assert after.headers["location"] == "/login"


def test_owner_setup_first_time_succeeds(client, tmp_db):
    token = get_csrf(client, "/owner-setup")
    resp = client.post(
        "/owner-setup",
        data={"setup_token": OWNER_SETUP_TOKEN, "password": OWNER_PASSWORD,
              "confirm_password": OWNER_PASSWORD, "csrf_token": token},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/owner"
    owner = _user_by_name(tmp_db, "zclips_owner")
    assert owner.role == "owner"
    assert owner.plan == "pro"


def test_owner_setup_second_time_returns_404(client, tmp_db):
    make_owner(tmp_db)
    assert client.get("/owner-setup").status_code == 404
    token = get_csrf(client, "/login")
    resp = client.post(
        "/owner-setup",
        data={"setup_token": OWNER_SETUP_TOKEN, "password": OWNER_PASSWORD,
              "confirm_password": OWNER_PASSWORD, "csrf_token": token},
    )
    assert resp.status_code == 404


def test_owner_setup_wrong_token_returns_403(client, tmp_db):
    token = get_csrf(client, "/owner-setup")
    resp = client.post(
        "/owner-setup",
        data={"setup_token": "wrong-token", "password": OWNER_PASSWORD,
              "confirm_password": OWNER_PASSWORD, "csrf_token": token},
    )
    assert resp.status_code == 403


def test_owner_setup_short_password_returns_400(client, tmp_db):
    token = get_csrf(client, "/owner-setup")
    resp = client.post(
        "/owner-setup",
        data={"setup_token": OWNER_SETUP_TOKEN, "password": "short-pass",
              "confirm_password": "short-pass", "csrf_token": token},
    )
    assert resp.status_code == 400
    assert "16 characters" in resp.text


def test_anonymous_dashboard_redirects_to_login(client, tmp_db):
    resp = client.get("/dashboard")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_anonymous_clips_redirects_to_login(client, tmp_db):
    resp = client.get("/clips")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_anonymous_owner_redirects_to_login(client, tmp_db):
    resp = client.get("/owner")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_customer_get_owner_returns_403(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    assert client.get("/owner").status_code == 403
