"""Tests for direct-to-backend video uploads (bypassing the Vercel proxy).

The PWA proxy rejects request bodies over ~4.5 MB
("Request Entity Too Large / FUNCTION_PAYLOAD_TOO_LARGE"), so file
uploads go DIRECTLY to the backend: the PWA first gets a one-time
upload token via the proxied POST /api/v1/clips/upload-token, then
POSTs the video to POST /api/v1/clips/direct-upload with that token
as a form field. The direct endpoint needs no session cookie and no
CSRF token — the token itself is the auth.
"""
import subprocess
import time

import pytest

import app.routers.api as api_router
import app.upload_tokens as upload_tokens
from app.config import STORAGE_DIR
from app.models import ClipJob, User
from conftest import login_as, make_user

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _clean_storage():
    before_files = {p for p in STORAGE_DIR.rglob("*") if p.is_file()}
    before_dirs = {p for p in STORAGE_DIR.rglob("*") if p.is_dir()}
    upload_tokens._clear_for_tests()
    yield
    upload_tokens._clear_for_tests()
    after_files = {p for p in STORAGE_DIR.rglob("*") if p.is_file()}
    for p in after_files - before_files:
        p.unlink(missing_ok=True)
    for d in sorted({p for p in STORAGE_DIR.rglob("*") if p.is_dir()} - before_dirs,
                    key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass


def _csrf(client):
    resp = client.get("/api/v1/csrf")
    assert resp.status_code == 200, resp.text[:300]
    return resp.json()["csrf_token"]


def _api_login(client, username, password):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": _csrf(client)},
    )


def _login_api_as(client, username, password):
    resp = _api_login(client, username, password)
    assert resp.status_code == 200, resp.text[:500]
    return resp


def _make_test_video(path, duration=2):
    subprocess.run(
        ["/usr/bin/ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i",
         f"testsrc2=size=320x180:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-shortest", "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True, timeout=120,
    )
    assert path.is_file() and path.stat().st_size > 0


def _issue_token(client):
    resp = client.post(
        "/api/v1/clips/upload-token",
        json={},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 200, resp.text[:500]
    data = resp.json()
    assert data["upload_token"]
    assert data["upload_url"].endswith("/api/v1/clips/direct-upload")
    return data["upload_token"]


def _direct_upload(client, video_bytes, token=None, extra_data=None):
    data = {
        "title": "Direct clip",
        "caption_style": "simple",
        "export_height": "1280",
        "facecam_mode": "auto",
    }
    if token is not None:
        data["upload_token"] = token
    if extra_data:
        data.update(extra_data)
    return client.post(
        "/api/v1/clips/direct-upload",
        data=data,
        files={"video": ("test.mp4", video_bytes, "video/mp4")},
    )


def _credits(session_factory, user_id):
    db = session_factory()
    try:
        return db.get(User, user_id).credits
    finally:
        db.close()


# ------------------------------------------------------------------ token
def test_upload_token_requires_auth(client, tmp_db):
    resp = client.post("/api/v1/clips/upload-token", json={})
    assert resp.status_code == 401


def test_upload_token_requires_csrf(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.post("/api/v1/clips/upload-token", json={})
    assert resp.status_code == 403


def test_upload_token_returns_token_and_url(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.post(
        "/api/v1/clips/upload-token",
        json={},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 200, resp.text[:500]
    data = resp.json()
    assert isinstance(data["upload_token"], str) and len(data["upload_token"]) >= 32
    assert data["upload_url"].endswith("/api/v1/clips/direct-upload")


# ------------------------------------------------------- direct upload: auth
def test_direct_upload_missing_token_is_401(client, tmp_db, tmp_path):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    resp = _direct_upload(client, video.read_bytes(), token=None)
    assert resp.status_code == 401


def test_direct_upload_invalid_token_is_401(client, tmp_db, tmp_path):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    resp = _direct_upload(client, video.read_bytes(), token="not-a-real-token")
    assert resp.status_code == 401


def test_direct_upload_expired_token_is_401(client, tmp_db, tmp_path):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    token = upload_tokens.issue_upload_token(alice)
    # Force-expire it in the store.
    upload_tokens._store[token] = (alice, time.monotonic() - 1)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    resp = _direct_upload(client, video.read_bytes(), token=token)
    assert resp.status_code == 401


def test_direct_upload_token_is_single_use(client, tmp_db, tmp_path):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    token = _issue_token(client)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    first = _direct_upload(client, video.read_bytes(), token=token)
    assert first.status_code == 201, first.text[:500]
    second = _direct_upload(client, video.read_bytes(), token=token)
    assert second.status_code == 401


# ------------------------------------------------- direct upload: happy path
def test_direct_upload_with_valid_token_creates_job_without_cookie(
    client, tmp_db, tmp_path
):
    """The whole point: no session cookie, no CSRF header — the token
    alone authenticates the direct upload."""
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    token = _issue_token(client)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    # Drop the session cookie: the direct upload must not need it.
    client.cookies.clear()
    resp = _direct_upload(client, video.read_bytes(), token=token)
    assert resp.status_code == 201, resp.text[:500]
    job = resp.json()["job"]
    assert job["status"] == "queued"
    assert job["title"] == "Direct clip"
    assert _credits(tmp_db, alice) == 4
    db = tmp_db()
    try:
        stored = db.get(ClipJob, job["id"])
        assert stored is not None
        assert stored.source_id is not None
    finally:
        db.close()


def test_direct_upload_url_source_with_valid_token(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    token = _issue_token(client)
    resp = client.post(
        "/api/v1/clips/direct-upload",
        data={
            "title": "Direct URL clip",
            "caption_style": "simple",
            "export_height": "1280",
            "facecam_mode": "auto",
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "upload_token": token,
        },
    )
    assert resp.status_code == 201, resp.text[:500]
    assert resp.json()["job"]["status"] == "queued"


# ------------------------------------------- direct upload: same validation
def test_direct_upload_free_user_editor_field_rejected(client, tmp_db, tmp_path):
    """Entitlements match the proxied create: a Free user cannot smuggle
    editor features through the direct endpoint."""
    make_user(tmp_db, username="alice", password=PASSWORD, plan="free")
    _login_api_as(client, "alice", PASSWORD)
    token = _issue_token(client)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    resp = _direct_upload(
        client, video.read_bytes(), token=token,
        extra_data={"aspect_ratio": "1:1"},
    )
    assert resp.status_code == 400
    assert "Lite or Pro" in resp.text


def test_direct_upload_bad_caption_style_rejected(client, tmp_db, tmp_path):
    make_user(tmp_db, username="alice", password=PASSWORD, plan="free")
    _login_api_as(client, "alice", PASSWORD)
    token = _issue_token(client)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    resp = _direct_upload(
        client, video.read_bytes(), token=token,
        extra_data={"caption_style": "neon"},
    )
    assert resp.status_code == 400


def test_direct_upload_file_too_large(client, tmp_db, tmp_path, monkeypatch):
    """The MAX_UPLOAD_MB cap is enforced on the direct path too."""
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    token = _issue_token(client)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    monkeypatch.setattr(api_router, "MAX_UPLOAD_MB", 0)
    resp = _direct_upload(client, video.read_bytes(), token=token)
    assert resp.status_code == 400
    assert "too large" in resp.text


def test_direct_upload_missing_title_is_400(client, tmp_db, tmp_path):
    make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    token = _issue_token(client)
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    resp = client.post(
        "/api/v1/clips/direct-upload",
        data={
            "title": "",
            "caption_style": "simple",
            "export_height": "1280",
            "facecam_mode": "auto",
            "upload_token": token,
        },
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
    )
    assert resp.status_code == 400


# ------------------------------------------------- proxied create unchanged
def test_proxied_create_still_works_after_refactor(client, tmp_db):
    """The refactor must not change the existing proxied endpoint."""
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    _login_api_as(client, "alice", PASSWORD)
    resp = client.post(
        "/api/v1/clips",
        data={"title": "API clip", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 201, resp.text[:500]
    assert resp.json()["job"]["status"] == "queued"
    assert _credits(tmp_db, alice) == 4
