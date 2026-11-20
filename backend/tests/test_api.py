"""Integration tests for the JSON API (``/api/v1/*``).

Covers the contract's core guarantees: 401 (never 303) for unauthenticated
calls, CSRF enforcement on state-changing requests, login/signup
validation, dashboard/clips, payment submit staying pending until the
owner approves, double-approval refusal, approved-only downloads, Pro
gating for YouTube, and owner-only endpoints.
"""
import pytest

import app.routers.api as api_router
from app.config import STORAGE_DIR
from app.models import Clip, ClipJob, PaymentRequest, SourceVideo, User
from conftest import login_as, make_owner, make_user

PASSWORD = "correct-horse-battery-staple"
OWNER_PASSWORD = "owner-password-that-is-long-enough"
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def _clean_storage():
    before_files = {p for p in STORAGE_DIR.rglob("*") if p.is_file()}
    before_dirs = {p for p in STORAGE_DIR.rglob("*") if p.is_dir()}
    yield
    after_files = {p for p in STORAGE_DIR.rglob("*") if p.is_file()}
    for p in after_files - before_files:
        p.unlink(missing_ok=True)
    for d in sorted({p for p in STORAGE_DIR.rglob("*") if p.is_dir()} - before_dirs,
                    key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass


def _db_get(session_factory, model, pk):
    db = session_factory()
    try:
        return db.get(model, pk)
    finally:
        db.close()


def _csrf(client):
    resp = client.get("/api/v1/csrf")
    assert resp.status_code == 200, resp.text[:300]
    assert "csrf_token" in resp.json()
    return resp.json()["csrf_token"]


def _api_login(client, username, password):
    token = _csrf(client)
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": token},
    )


def _login_api_as(client, username, password):
    resp = _api_login(client, username, password)
    assert resp.status_code == 200, resp.text[:500]
    return resp


def _headers(client):
    return {"X-CSRF-Token": _csrf(client)}


def _insert_clip(session_factory, user_id, title, status="candidate", file_path=""):
    db = session_factory()
    try:
        src = SourceVideo(user_id=user_id, title=title, source_type="upload",
                          stored_path="", status="uploaded")
        db.add(src)
        db.flush()
        job = ClipJob(user_id=user_id, source_id=src.id, title=title,
                      status="done", progress=100)
        db.add(job)
        db.flush()
        clip = Clip(job_id=job.id, user_id=user_id, source_id=src.id,
                    title=title, start_s=0.0, end_s=10.0, duration_s=10.0,
                    status=status, qc_passed=True, file_path=file_path)
        db.add(clip)
        db.commit()
        return clip.id
    finally:
        db.close()


def _submit_payment_api(client, plan="lite", txn_id="TXN-API-1"):
    return client.post(
        "/api/v1/pay",
        data={"plan": plan, "txn_id": txn_id},
        files={"screenshot": ("shot.png", PNG_1X1, "image/png")},
        headers={"X-CSRF-Token": _csrf(client)},
    )


# ------------------------------------------------------------------ meta/auth
def test_health_no_auth(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "z-clips"}


def test_csrf_returns_token(client):
    resp = client.get("/api/v1/csrf")
    assert resp.status_code == 200
    assert resp.json()["csrf_token"]


def test_unauthenticated_me_is_401_not_303(client):
    resp = client.get("/api/v1/me")
    assert resp.status_code == 401
    assert resp.status_code != 303
    # Contract error shape (not FastAPI's default {"detail": ...}).
    assert resp.json() == {"error": "Please sign in."}


def test_unauthenticated_dashboard_is_401(client):
    assert client.get("/api/v1/dashboard").status_code == 401


def test_login_success_returns_user_summary(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    resp = _api_login(client, "alice", PASSWORD)
    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["username"] == "alice"
    assert user["role"] == "customer"
    assert user["plan"] == "free"
    assert "password_hash" not in resp.json()["user"]
    assert "password_hash" not in resp.text
    # Session cookie set: /me now works.
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"
    assert "entitlements" in me.json()


def test_login_wrong_password_is_401(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    resp = _api_login(client, "alice", "wrong-password-here")
    assert resp.status_code == 401
    assert "Username ya password sahi nahi hai." in resp.text


def test_login_missing_csrf_is_403(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    client.get("/api/v1/csrf")  # establish a session, but send no token
    resp = client.post("/api/v1/auth/login",
                       json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 403


def test_signup_success_creates_customer(client, tmp_db):
    resp = client.post(
        "/api/v1/auth/signup",
        json={"username": "newuser", "email": "new@example.com",
              "password": "a-very-long-password-123"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 201, resp.text[:300]
    user = resp.json()["user"]
    assert user["username"] == "newuser"
    assert user["role"] == "customer"
    assert user["plan"] == "free"
    assert client.get("/api/v1/me").status_code == 200


def test_signup_validation_errors(client, tmp_db):
    # Bad username.
    resp = client.post("/api/v1/auth/signup",
                       json={"username": "ab", "email": "a@example.com",
                             "password": "a-very-long-password-123"},
                       headers={"X-CSRF-Token": _csrf(client)})
    assert resp.status_code == 400
    # Short password.
    resp = client.post("/api/v1/auth/signup",
                       json={"username": "goodname", "email": "b@example.com",
                             "password": "short"},
                       headers={"X-CSRF-Token": _csrf(client)})
    assert resp.status_code == 400
    # Duplicate username.
    make_user(tmp_db, username="alice", password=PASSWORD)
    resp = client.post("/api/v1/auth/signup",
                       json={"username": "alice", "email": "other@example.com",
                             "password": "a-very-long-password-123"},
                       headers={"X-CSRF-Token": _csrf(client)})
    assert resp.status_code == 400


def test_logout_clears_session(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    assert client.get("/api/v1/me").status_code == 200
    resp = client.post("/api/v1/auth/logout", headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert client.get("/api/v1/me").status_code == 401


def test_csrf_missing_on_post_is_403(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.post("/api/v1/support", json={"subject": "Help", "body": "x"})
    assert resp.status_code == 403
    assert resp.json()["error"]


# ------------------------------------------------------------------ dashboard
def test_dashboard_returns_user_and_credits(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.get("/api/v1/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["username"] == "alice"
    assert body["credits"] == 5
    assert body["plan_name"] == "Free"
    assert body["clip_counts"] == {"total": 0, "approved": 0, "candidates": 0}
    assert body["yt_status"] == "locked"
    assert body["subscription"] is None
    assert body["promotion"]["name"]
    assert body["recent_activity"] == []


def test_plans_no_auth(client, tmp_db):
    resp = client.get("/api/v1/plans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plans"]["lite"]["price_pkr"] == 1500
    assert body["plans"]["pro"]["price_pkr"] == 7000
    assert body["promotion"]["promo_price_pkr"] == 5250


def test_pay_info_amounts_server_side(client, tmp_db, monkeypatch, tmp_path):
    # Point the QR at a temp path with no file: this test asserts the
    # "no QR provided" state and must not depend on the real shipped QR.
    monkeypatch.setattr(api_router, "NAYAPAY_QR_PATH", str(tmp_path / "nayapay-qr.png"))
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.get("/api/v1/pay")
    assert resp.status_code == 200
    body = resp.json()
    assert body["amounts"] == {"lite": 1500, "pro": 5250}
    assert body["account_holder"] == "Zain ul abideen"
    assert body["number"] == "03296195965"
    assert body["qr_available"] is False


# ------------------------------------------------------------------ clips
def test_clips_list_and_create_from_url(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.get("/api/v1/clips")
    assert resp.status_code == 200
    assert resp.json() == {"clips": [], "jobs": []}

    resp = client.post(
        "/api/v1/clips",
        data={"title": "API clip", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "source_url": YOUTUBE_URL},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 201, resp.text[:500]
    job = resp.json()["job"]
    assert job["status"] == "queued"
    assert job["title"] == "API clip"
    assert _db_get(tmp_db, User, alice).credits == 4

    listing = client.get("/api/v1/clips").json()
    assert len(listing["jobs"]) == 1
    job_page = client.get(f"/api/v1/clips/jobs/{job['id']}").json()
    assert job_page["job"]["id"] == job["id"]
    assert job_page["source"]["source_type"] == "url"


def test_clip_create_bad_host_is_400(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.post(
        "/api/v1/clips",
        data={"title": "Bad host", "source_url": "https://example.com/v.mp4"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 400
    assert "not supported" in resp.text


def test_clip_detail_and_approve_reject(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    clip_id = _insert_clip(tmp_db, alice, "Detail clip")
    _login_api_as(client, "alice", PASSWORD)
    resp = client.get(f"/api/v1/clips/{clip_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["clip"]["title"] == "Detail clip"
    assert body["clip"]["thumb_url"] is None
    assert body["clip"]["preview_url"] is None
    assert body["can_edit"] is False  # free plan has no manual editing

    resp = client.post(f"/api/v1/clips/{clip_id}/approve", headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["clip"]["status"] == "approved"
    resp = client.post(f"/api/v1/clips/{clip_id}/reject", headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["clip"]["status"] == "rejected"


def test_clip_preview_missing_file_is_404(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    clip_id = _insert_clip(tmp_db, alice, "No file clip")
    _login_api_as(client, "alice", PASSWORD)
    assert client.get(f"/api/v1/clips/{clip_id}/preview").status_code == 404
    assert client.get(f"/api/v1/clips/{clip_id}/thumb").status_code == 404


# ------------------------------------------------------------------ downloads
def test_download_unapproved_clip_is_403(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    clip_id = _insert_clip(tmp_db, alice, "Candidate clip")
    _login_api_as(client, "alice", PASSWORD)
    assert client.get(f"/api/v1/downloads/{clip_id}").status_code == 403
    assert client.get("/api/v1/downloads").json() == {"clips": []}


def test_download_approved_clip_streams_file(client, tmp_db, tmp_path):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    clip_id = _insert_clip(tmp_db, alice, "Approved clip",
                           status="approved", file_path=str(video))
    _login_api_as(client, "alice", PASSWORD)
    resp = client.get("/api/v1/downloads")
    assert len(resp.json()["clips"]) == 1
    dl = client.get(f"/api/v1/downloads/{clip_id}")
    assert dl.status_code == 200
    assert dl.content == b"fake-mp4-bytes"
    assert _db_get(tmp_db, Clip, clip_id).downloads == 1


# ------------------------------------------------------------------ payments
def test_payment_submit_creates_pending_not_active(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = _submit_payment_api(client, plan="lite", txn_id="TXN-API-LITE")
    assert resp.status_code == 201, resp.text[:500]
    payment = resp.json()["payment"]
    assert payment["status"] == "pending"
    assert payment["amount_pkr"] == 1500
    assert payment["txn_id"] == "TXN-API-LITE"
    # Plan is NOT activated by submission.
    assert _db_get(tmp_db, User, alice).plan == "free"
    history = client.get("/api/v1/payments").json()["payments"]
    assert len(history) == 1
    waiting = client.get(f"/api/v1/pay/waiting/{payment['id']}").json()
    assert waiting["payment"]["id"] == payment["id"]


def test_owner_approve_activates_once_second_approve_fails(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    pid = _submit_payment_api(client, plan="lite", txn_id="TXN-API-A1").json()["payment"]["id"]
    make_owner(tmp_db)
    _login_api_as(client, "zclips_owner", OWNER_PASSWORD)
    resp = client.post(f"/api/v1/owner/payments/{pid}/approve",
                       headers=_headers(client))
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["payment"]["status"] == "approved"
    user = _db_get(tmp_db, User, alice)
    assert user.plan == "lite"
    assert user.credits == 20
    second = client.post(f"/api/v1/owner/payments/{pid}/approve",
                         headers=_headers(client))
    assert second.status_code in (400, 409)
    assert "already been approved" in second.text
    assert _db_get(tmp_db, User, alice).credits == 20
    assert _db_get(tmp_db, PaymentRequest, pid).status == "approved"


def test_owner_reject_payment(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    pid = _submit_payment_api(client, plan="lite", txn_id="TXN-API-R1").json()["payment"]["id"]
    make_owner(tmp_db)
    _login_api_as(client, "zclips_owner", OWNER_PASSWORD)
    resp = client.post(f"/api/v1/owner/payments/{pid}/reject",
                       json={"note": "not received"},
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["payment"]["status"] == "rejected"
    assert _db_get(tmp_db, User, alice).plan == "free"


def test_pay_qr_missing_is_404_json(client, tmp_db, monkeypatch, tmp_path):
    # Point the QR at a temp path with no file: this test asserts the
    # "no QR provided" state and must not depend on the real shipped QR.
    monkeypatch.setattr(api_router, "NAYAPAY_QR_PATH", str(tmp_path / "nayapay-qr.png"))
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.get("/api/v1/pay/qr")
    assert resp.status_code == 404
    assert resp.json()["error"] == "QR code not yet provided by the owner."


# ------------------------------------------------------------------ support
def test_support_create_reply_flow(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.post("/api/v1/support",
                       json={"subject": "Need help", "body": "My clip failed."},
                       headers=_headers(client))
    assert resp.status_code == 201, resp.text[:300]
    thread = resp.json()["thread"]
    assert thread["status"] == "open"
    assert client.get("/api/v1/support").json()["threads"][0]["id"] == thread["id"]
    resp = client.post(f"/api/v1/support/{thread['id']}/reply",
                       json={"body": "More detail here."},
                       headers=_headers(client))
    assert resp.status_code == 201
    assert resp.json()["message"]["body"] == "More detail here."
    detail = client.get(f"/api/v1/support/{thread['id']}").json()
    assert len(detail["messages"]) == 2


# ------------------------------------------------------------------ yt
def test_yt_without_pro_is_403(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.get("/api/v1/yt")
    assert resp.status_code == 200
    assert resp.json()["youtube_monitoring"] is False
    resp = client.post("/api/v1/yt/channels",
                       json={"channel_url": "https://www.youtube.com/@someone"},
                       headers=_headers(client))
    assert resp.status_code == 403


def test_yt_pro_user_can_add_channel(client, tmp_db):
    make_user(tmp_db, username="prouser", email="pro@example.com",
              password=PASSWORD, plan="pro", credits=70)
    _login_api_as(client, "prouser", PASSWORD)
    resp = client.post("/api/v1/yt/channels",
                       json={"channel_url": "https://www.youtube.com/@someone"},
                       headers=_headers(client))
    assert resp.status_code == 201, resp.text[:500]
    channel = resp.json()["channel"]
    assert channel["channel_handle"] == "@someone"
    assert channel["ingestion_state"] == "waiting_authorized"
    listing = client.get("/api/v1/yt").json()
    assert listing["youtube_monitoring"] is True
    assert len(listing["channels"]) == 1
    # Authorize + delete round-trip.
    resp = client.post(f"/api/v1/yt/channels/{channel['id']}/authorize",
                       json={"note": "my own channel"},
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["channel"]["ingestion_state"] == "authorized"
    resp = client.post(f"/api/v1/yt/channels/{channel['id']}/delete",
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ------------------------------------------------------------------ settings
def test_settings_get_and_update(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["default_caption_style"] == "simple"
    assert resp.json()["default_export_height"] == 1280
    resp = client.post("/api/v1/settings",
                       json={"display_name": "Alice A", "email": "alice@example.com",
                             "default_caption_style": "simple",
                             "default_export_height": 1280},
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["user"]["display_name"] == "Alice A"


# ------------------------------------------------------------------ owner
def test_owner_endpoints_forbidden_for_customers(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    assert client.get("/api/v1/owner/overview").status_code == 403
    assert client.get("/api/v1/owner/payments").status_code == 403
    assert client.get("/api/v1/owner/users").status_code == 403
    assert client.get("/api/v1/owner/system").status_code == 403
    assert client.get("/api/v1/owner/promotion").status_code == 403
    assert client.get("/api/v1/owner/settings").status_code == 403


def test_owner_endpoints_unauthenticated_are_401(client):
    assert client.get("/api/v1/owner/overview").status_code == 401


def test_owner_overview_users_and_promotion(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    make_owner(tmp_db)
    _login_api_as(client, "zclips_owner", OWNER_PASSWORD)
    overview = client.get("/api/v1/owner/overview").json()
    assert overview["counts"]["users"] == 1
    assert overview["counts"]["pending_payments"] == 0
    users = client.get("/api/v1/owner/users").json()["users"]
    assert len(users) == 1 and users[0]["username"] == "alice"
    detail = client.get(f"/api/v1/owner/users/{alice}").json()
    assert detail["user"]["username"] == "alice"
    assert detail["subscriptions"] == []
    promo = client.get("/api/v1/owner/promotion").json()
    assert promo["promotion"]["max_seats"] == 20
    assert promo["promo_active"] is True
    system = client.get("/api/v1/owner/system").json()
    assert "health" in system and "models" in system and "storage" in system
    settings = client.get("/api/v1/owner/settings").json()
    assert settings == {"settings": {}}


def test_owner_user_credits_plan_extend(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    make_owner(tmp_db)
    _login_api_as(client, "zclips_owner", OWNER_PASSWORD)
    resp = client.post(f"/api/v1/owner/users/{alice}/credits",
                       json={"delta": 3, "reason": "goodwill"},
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["user"]["credits"] == 8
    resp = client.post(f"/api/v1/owner/users/{alice}/plan",
                       json={"plan": "lite"},
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["user"]["plan"] == "lite"
    assert resp.json()["user"]["credits"] == 20
    resp = client.post(f"/api/v1/owner/users/{alice}/extend",
                       json={"days": 5},
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["user"]["plan_ends_at"] is not None
    resp = client.post(f"/api/v1/owner/users/{alice}/status",
                       json={"is_suspended": True},
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["user"]["is_suspended"] is True


def test_owner_support_reply_and_close(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    tid = client.post("/api/v1/support",
                      json={"subject": "Help", "body": "Issue"},
                      headers=_headers(client)).json()["thread"]["id"]
    make_owner(tmp_db)
    _login_api_as(client, "zclips_owner", OWNER_PASSWORD)
    threads = client.get("/api/v1/owner/support").json()["threads"]
    assert len(threads) == 1
    resp = client.post(f"/api/v1/owner/support/{tid}/reply",
                       json={"body": "We are on it."},
                       headers=_headers(client))
    assert resp.status_code == 201
    resp = client.post(f"/api/v1/owner/support/{tid}/close",
                       headers=_headers(client))
    assert resp.status_code == 200
    assert resp.json()["thread"]["status"] == "closed"


def test_search_owner_sees_users_customer_does_not(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _insert_clip(tmp_db, alice, "Alice Sunset Vlog")
    _login_api_as(client, "alice", PASSWORD)
    body = client.get("/api/v1/search?q=Sunset").json()
    assert len(body["clips"]) == 1
    assert body["users"] == []
    make_owner(tmp_db)
    _login_api_as(client, "zclips_owner", OWNER_PASSWORD)
    body = client.get("/api/v1/search?q=alice").json()
    assert len(body["users"]) == 1
    assert body["users"][0]["username"] == "alice"
