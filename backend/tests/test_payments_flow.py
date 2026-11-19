"""Integration tests: NayaPay payment submission, waiting page, owner review."""
import pytest

from app.config import STORAGE_DIR
from app.models import PaymentRequest, Promotion, User
from conftest import get_csrf, login_as, make_owner, make_user

PASSWORD = "correct-horse-battery-staple"
OWNER_PASSWORD = "owner-password-that-is-long-enough"

# Minimal valid 1x1 PNG.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


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


def _get(session_factory, model, pk):
    db = session_factory()
    try:
        return db.get(model, pk)
    finally:
        db.close()


def _user(session_factory, user_id):
    return _get(session_factory, User, user_id)


def _payment(session_factory, payment_id):
    return _get(session_factory, PaymentRequest, payment_id)


def _promotion(session_factory):
    return _get(session_factory, Promotion, 1)


def submit_payment(client, plan="lite", txn_id="TXN-1", screenshot=True,
                   filename="shot.png", content=PNG_1X1):
    """Submit a payment as the logged-in user; return the response."""
    token = get_csrf(client, f"/pay?plan={plan}")
    data = {"plan": plan, "txn_id": txn_id, "csrf_token": token}
    files = {"screenshot": (filename, content, "image/png")} if screenshot else None
    if files is not None:
        return client.post("/pay", data=data, files=files)
    return client.post("/pay", data=data)


def _payment_id_from(resp):
    assert resp.status_code == 303, resp.text[:500]
    return int(resp.headers["location"].rsplit("/", 1)[-1])


def _login_owner(client, tmp_db):
    make_owner(tmp_db)
    login_as(client, tmp_db, "zclips_owner", OWNER_PASSWORD)


# ------------------------------------------------------------------ pay page
def test_pay_free_redirects_to_plans(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/pay?plan=free")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/plans"


def test_pay_lite_shows_pkr_1500(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/pay?plan=lite")
    assert resp.status_code == 200
    assert "1,500" in resp.text


def test_pay_pro_shows_promo_price_5250_while_promotion_active(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/pay?plan=pro")
    assert resp.status_code == 200
    assert "5,250" in resp.text
    promo = _promotion(tmp_db)
    assert promo is not None and promo.seats_reserved == 0


# ------------------------------------------------------------------ submit
def test_pay_submit_without_txn_id_returns_400(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    # Whitespace-only: an empty-string txn_id is dropped by multipart
    # encoding (FastAPI then 422s on the missing field), while whitespace
    # reaches the app's own validation and its 400 "Enter the NayaPay
    # transaction ID." path.
    resp = submit_payment(client, plan="lite", txn_id="   ")
    assert resp.status_code == 400
    assert "transaction ID" in resp.text


def test_pay_submit_without_screenshot_returns_400(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = submit_payment(client, plan="lite", screenshot=False)
    assert resp.status_code == 400
    assert "screenshot" in resp.text.lower()


def test_pay_submit_with_bad_screenshot_extension_returns_400(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = submit_payment(client, plan="lite", filename="shot.txt",
                          content=b"not an image")
    assert resp.status_code == 400
    assert "PNG, JPG, JPEG or WebP" in resp.text


def test_valid_lite_submit_creates_pending_payment(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = submit_payment(client, plan="lite", txn_id="TXN-LITE-1")
    pid = _payment_id_from(resp)
    assert resp.headers["location"] == f"/pay/waiting/{pid}"
    payment = _payment(tmp_db, pid)
    assert payment is not None
    assert payment.status == "pending"
    assert payment.user_id == alice
    assert payment.plan == "lite"
    assert payment.amount_pkr == 1500
    assert payment.txn_id == "TXN-LITE-1"


def test_valid_pro_submit_reserves_promo_seat(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = submit_payment(client, plan="pro", txn_id="TXN-PRO-1")
    pid = _payment_id_from(resp)
    payment = _payment(tmp_db, pid)
    assert payment.status == "pending"
    assert payment.is_promotional is True
    assert payment.amount_pkr == 5250
    promo = _promotion(tmp_db)
    assert promo.seats_reserved == 1
    assert promo.seats_used == 0


# ------------------------------------------------------------------ waiting
def test_waiting_page_forbidden_for_other_user(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    make_user(tmp_db, username="bob", email="bob@example.com", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    pid = _payment_id_from(submit_payment(client, plan="lite", txn_id="TXN-A1"))
    login_as(client, tmp_db, "bob", PASSWORD)
    assert client.get(f"/pay/waiting/{pid}").status_code == 403


def test_waiting_page_missing_id_returns_404(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    assert client.get("/pay/waiting/999999").status_code == 404


# ------------------------------------------------------------------ review
def test_owner_approves_lite_payment_grants_plan_and_credits(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    pid = _payment_id_from(submit_payment(client, plan="lite", txn_id="TXN-A1"))
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/payments/{pid}")
    resp = client.post(f"/owner/payments/{pid}/approve",
                       data={"csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/owner/payments/{pid}"
    assert _payment(tmp_db, pid).status == "approved"
    user = _user(tmp_db, alice)
    assert user.plan == "lite"
    assert user.credits == 20


def test_double_approval_returns_400_and_credits_unchanged(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    pid = _payment_id_from(submit_payment(client, plan="lite", txn_id="TXN-A1"))
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/payments/{pid}")
    first = client.post(f"/owner/payments/{pid}/approve",
                        data={"csrf_token": token})
    assert first.status_code == 303
    assert _user(tmp_db, alice).credits == 20
    # The detail page shows no approve form once the payment is decided,
    # so there is no fresh token to fetch — but tokens are per-session and
    # reusable, so the first token is valid for the second attempt.
    second = client.post(f"/owner/payments/{pid}/approve",
                         data={"csrf_token": token})
    assert second.status_code == 400
    assert "already been approved" in second.text
    assert _user(tmp_db, alice).credits == 20
    assert _payment(tmp_db, pid).status == "approved"


def test_owner_rejects_pro_payment_releases_promo_seat(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    pid = _payment_id_from(submit_payment(client, plan="pro", txn_id="TXN-P1"))
    assert _promotion(tmp_db).seats_reserved == 1
    _login_owner(client, tmp_db)
    token = get_csrf(client, f"/owner/payments/{pid}")
    resp = client.post(f"/owner/payments/{pid}/reject",
                       data={"note": "not received", "csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/owner/payments"
    payment = _payment(tmp_db, pid)
    assert payment.status == "rejected"
    assert _promotion(tmp_db).seats_reserved == 0


def test_payments_history_shows_only_own_payments(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    make_user(tmp_db, username="bob", email="bob@example.com", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    submit_payment(client, plan="lite", txn_id="TXN-ALICE-ONLY")
    login_as(client, tmp_db, "bob", PASSWORD)
    submit_payment(client, plan="lite", txn_id="TXN-BOB-ONLY")
    resp = client.get("/payments")
    assert resp.status_code == 200
    assert "TXN-BOB-ONLY" in resp.text
    assert "TXN-ALICE-ONLY" not in resp.text
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/payments")
    assert "TXN-ALICE-ONLY" in resp.text
    assert "TXN-BOB-ONLY" not in resp.text
