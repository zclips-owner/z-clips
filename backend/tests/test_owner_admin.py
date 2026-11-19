"""Integration tests: owner admin — access control, user management,
promotion clamping, settings QR upload, CSRF on every owner POST."""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import NAYAPAY_QR_PATH, STORAGE_DIR
from app.models import Promotion, User
from app.services import billing
from conftest import get_csrf, login_as, make_owner, make_user

PASSWORD = "correct-horse-battery-staple"
OWNER_PASSWORD = "owner-password-that-is-long-enough"

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

OWNER_PAGES = [
    "/owner", "/owner/payments", "/owner/users",
    "/owner/promotion", "/owner/system", "/owner/settings",
]


@pytest.fixture(autouse=True)
def _clean_storage():
    """Remove any files the test writes under the real storage dir."""
    before_files = {p for p in STORAGE_DIR.rglob("*") if p.is_file()}
    before_dirs = {p for p in STORAGE_DIR.rglob("*") if p.is_dir()}
    yield
    after_files = {p for p in STORAGE_DIR.rglob("*") if p.is_file()}
    for p in after_files - before_files:
        p.unlink(missing_ok=True)
    # Only remove directories this test created (never pre-existing ones).
    for d in sorted({p for p in STORAGE_DIR.rglob("*") if p.is_dir()} - before_dirs,
                    key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass


def _db(session_factory):
    return session_factory()


def _user(session_factory, user_id):
    db = _db(session_factory)
    try:
        return db.get(User, user_id)
    finally:
        db.close()


def _login_owner(client, tmp_db):
    make_owner(tmp_db)
    login_as(client, tmp_db, "zclips_owner", OWNER_PASSWORD)


def _as_aware(value):
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def test_customer_cannot_access_any_owner_page(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    for path in OWNER_PAGES:
        assert client.get(path).status_code == 403, path


def test_owner_dashboard_shows_real_stats(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    make_user(tmp_db, username="bob", email="bob@example.com", password=PASSWORD)
    _login_owner(client, tmp_db)
    resp = client.get("/owner")
    assert resp.status_code == 200
    assert "Total users" in resp.text
    # Two customers exist; the stat is rendered from the live DB.
    assert ">2<" in resp.text


def test_owner_suspends_user_who_can_no_longer_use_account(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/users/{alice}")
    resp = client.post(f"/owner/users/{alice}/status",
                       data={"action": "suspend", "csrf_token": token})
    assert resp.status_code == 303
    assert _user(tmp_db, alice).is_suspended is True
    # A suspended account can no longer reach any authenticated page: the
    # session is cleared on the next authenticated request.
    login_as(client, tmp_db, "alice", PASSWORD)
    after = client.get("/dashboard")
    assert after.status_code == 303
    assert after.headers["location"] == "/login"


def test_owner_plan_change_without_confirm_returns_400(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/users/{alice}")
    resp = client.post(f"/owner/users/{alice}/plan",
                       data={"plan": "lite", "confirm": "", "csrf_token": token})
    assert resp.status_code == 400
    assert _user(tmp_db, alice).plan == "free"


def test_owner_plan_change_with_confirm_updates_plan_and_credits(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/users/{alice}")
    resp = client.post(f"/owner/users/{alice}/plan",
                       data={"plan": "lite", "confirm": "yes",
                             "csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/owner/users/{alice}"
    user = _user(tmp_db, alice)
    assert user.plan == "lite"
    assert user.credits == 20
    assert user.plan_ends_at is not None


def test_owner_credits_negative_delta_clamps_at_zero(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD, credits=5)
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/users/{alice}")
    resp = client.post(f"/owner/users/{alice}/credits",
                       data={"delta": "-10", "reason": "test adjustment",
                             "csrf_token": token})
    assert resp.status_code == 303
    assert _user(tmp_db, alice).credits == 0


def test_owner_extends_subscription(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/users/{alice}")
    resp = client.post(f"/owner/users/{alice}/plan",
                       data={"plan": "lite", "confirm": "yes",
                             "csrf_token": token})
    assert resp.status_code == 303
    old_ends = _as_aware(_user(tmp_db, alice).plan_ends_at)
    assert old_ends is not None
    token = get_csrf(client, f"/owner/users/{alice}")
    resp = client.post(f"/owner/users/{alice}/extend",
                       data={"days": "10", "csrf_token": token})
    assert resp.status_code == 303
    new_ends = _as_aware(_user(tmp_db, alice).plan_ends_at)
    assert abs((new_ends - old_ends) - timedelta(days=10)) < timedelta(seconds=60)


def test_owner_toggles_complimentary_premium(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/users/{alice}")
    resp = client.post(f"/owner/users/{alice}/complimentary",
                       data={"enable": "on", "csrf_token": token})
    assert resp.status_code == 303
    assert _user(tmp_db, alice).complimentary_premium is True
    token = get_csrf(client, f"/owner/users/{alice}")
    resp = client.post(f"/owner/users/{alice}/complimentary",
                       data={"enable": "off", "csrf_token": token})
    assert resp.status_code == 303
    assert _user(tmp_db, alice).complimentary_premium is False


def test_owner_promotion_clamps_max_seats_below_reserved_plus_used(client, tmp_db):
    db = _db(tmp_db)
    try:
        assert billing.reserve_promo_seat(db) is True
        db.commit()
    finally:
        db.close()
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_owner(client, tmp_db)
    token = get_csrf(client, "/owner/promotion")
    resp = client.post(
        "/owner/promotion",
        data={"max_seats": "0", "ends_at": "2026-11-30T23:59",
              "is_active": "on", "csrf_token": token},
    )
    assert resp.status_code == 303
    db = _db(tmp_db)
    try:
        promo = db.get(Promotion, 1)
        # Floor is seats_reserved (1) + seats_used (0); max_seats may never
        # drop below it.
        assert promo.max_seats == 1
        assert promo.seats_reserved == 1
    finally:
        db.close()


def test_owner_settings_qr_upload_accepts_image(client, tmp_db):
    _login_owner(client, tmp_db)
    token = get_csrf(client, "/owner/settings")
    resp = client.post(
        "/owner/settings",
        data={"support_email": "help@example.com", "announcement": "",
              "csrf_token": token},
        files={"qr": ("qr.png", PNG_1X1, "image/png")},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/owner/settings"
    from pathlib import Path
    qr = Path(NAYAPAY_QR_PATH)
    assert qr.is_file()
    assert qr.read_bytes() == PNG_1X1


def test_owner_posts_without_csrf_return_403(client, tmp_db):
    from app.models import PaymentRequest, SupportThread
    from test_payments_flow import submit_payment

    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = submit_payment(client, plan="lite", txn_id="TXN-CSRF-1")
    pid = int(resp.headers["location"].rsplit("/", 1)[-1])
    token = get_csrf(client, "/support/new")
    thread_resp = client.post(
        "/support/new",
        data={"subject": "CSRF thread", "message": "hello", "csrf_token": token},
    )
    tid = int(thread_resp.headers["location"].rsplit("/", 1)[-1])
    _login_owner(client, tmp_db)

    # "Without CSRF" is expressed as an invalid token value: the test
    # client drops empty-string form values, so csrf_token="" never reaches
    # the app (FastAPI 422s on the missing required field instead). An
    # invalid token reaches the app's validate_csrf and must yield 403.
    no_csrf = {"csrf_token": "not-a-real-token"}
    posts = [
        (f"/owner/payments/{pid}/approve", dict(no_csrf)),
        (f"/owner/payments/{pid}/reject", {"note": "x", **no_csrf}),
        (f"/owner/users/{alice}/status", {"action": "suspend", **no_csrf}),
        (f"/owner/users/{alice}/plan",
         {"plan": "lite", "confirm": "yes", **no_csrf}),
        (f"/owner/users/{alice}/credits",
         {"delta": "5", "reason": "test", **no_csrf}),
        (f"/owner/users/{alice}/extend", {"days": "5", **no_csrf}),
        (f"/owner/users/{alice}/complimentary", {"enable": "on", **no_csrf}),
        ("/owner/promotion",
         {"max_seats": "20", "ends_at": "2026-11-30T23:59",
          "is_active": "on", **no_csrf}),
        ("/owner/settings",
         {"support_email": "a@b.c", "announcement": "", **no_csrf}),
        (f"/owner/support/{tid}/reply", {"message": "hi", **no_csrf}),
        (f"/owner/support/{tid}/close", dict(no_csrf)),
        ("/owner/system/models/whisper-tiny/install", dict(no_csrf)),
    ]
    for path, data in posts:
        resp = client.post(path, data=data)
        assert resp.status_code == 403, f"{path} -> {resp.status_code}"
    # Nothing was changed by the rejected posts.
    db = _db(tmp_db)
    try:
        assert db.get(PaymentRequest, pid).status == "pending"
        assert db.get(User, alice).is_suspended is False
        assert db.get(SupportThread, tid).status == "open"
    finally:
        db.close()
