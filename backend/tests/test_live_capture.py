"""Tests: live-stream clip extraction (app/services/live_capture.py).

Network-free: yt-dlp / ffmpeg recording are stubbed with monkeypatch,
except score_segment_file, which runs the real pipeline scorer on a
small ffmpeg-generated file (no network).
"""
import subprocess
import time
from pathlib import Path

import pytest

from app.models import ClipJob, SourceVideo, YTChannel
from app.services import live_capture as lc
from conftest import get_csrf, login_as, make_user

PASSWORD = "correct-horse-battery-staple"


def _db(session_factory):
    return session_factory()


def _make_channel(session_factory, user_id, authorized=True):
    db = _db(session_factory)
    try:
        ch = YTChannel(
            user_id=user_id,
            channel_url="https://www.youtube.com/@testchannel",
            channel_handle="@testchannel",
            channel_id="UCtest123",
            ingestion_state="authorized" if authorized else "waiting_authorized",
            live_video_id="",
            live_title="",
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)
        return ch.id
    finally:
        db.close()


def _channel(session_factory, channel_id):
    db = _db(session_factory)
    try:
        return db.get(YTChannel, channel_id)
    finally:
        db.close()


def _jobs(session_factory, user_id):
    db = _db(session_factory)
    try:
        return db.query(ClipJob).filter(ClipJob.user_id == user_id).all()
    finally:
        db.close()


def _gen_video(path: Path, seconds: int = 25) -> Path:
    """Small real mp4 (moving test pattern + tone) for the scorer."""
    subprocess.run(
        ["/usr/bin/ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=640x360:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(path)],
        check=True, timeout=120,
    )
    return path


def _make_source(session_factory, user_id, stored_path: str):
    db = _db(session_factory)
    try:
        src = SourceVideo(
            user_id=user_id, title="segment", source_type="upload",
            stored_path=stored_path, status="uploaded",
        )
        db.add(src)
        db.commit()
        db.refresh(src)
        return src.id
    finally:
        db.close()


def test_extract_info_bounded_times_out(monkeypatch):
    def slow(url):
        time.sleep(30)
        return {}

    monkeypatch.setattr(lc, "_extract_info", slow)
    t0 = time.time()
    with pytest.raises(lc.LiveCaptureError, match="timed out"):
        lc._extract_info_bounded("https://example.invalid", timeout=0.2)
    assert time.time() - t0 < 5


# ---------------------------------------------------------------- hook
def test_hook_skips_unauthorized_channel(tmp_db, monkeypatch):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=False)
    started = []
    monkeypatch.setattr(lc, "start_live_capture", lambda *a, **k: started.append(True))
    db = _db(tmp_db)
    try:
        ch = db.get(YTChannel, ch_id)
        out = lc.on_live_status_change(
            db, ch,
            {"status": "live", "live_video_id": "abc123", "live_title": "Live!"},
        )
    finally:
        db.close()
    assert out["action"] == "skipped"
    assert out["reason"] == "channel_not_authorized"
    assert started == []
    assert _jobs(tmp_db, user_id) == []


def test_hook_live_starts_capture_for_authorized_channel(tmp_db, monkeypatch):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    calls = []

    def fake_start(channel_id, uid, vid, **k):
        calls.append((channel_id, uid, vid))
        return True

    monkeypatch.setattr(lc, "start_live_capture", fake_start)
    db = _db(tmp_db)
    try:
        ch = db.get(YTChannel, ch_id)
        out = lc.on_live_status_change(
            db, ch,
            {"status": "live", "live_video_id": "abc123", "live_title": "Live!"},
        )
        db.commit()
    finally:
        db.close()
    assert out == {"action": "capture_started", "video_id": "abc123"}
    assert calls == [(ch_id, user_id, "abc123")]


def test_hook_offline_vod_not_ready_creates_nothing(tmp_db, monkeypatch):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    monkeypatch.setattr(lc, "vod_is_available", lambda vid: False)
    db = _db(tmp_db)
    try:
        ch = db.get(YTChannel, ch_id)
        out = lc.on_live_status_change(
            db, ch, {"status": "offline"},
            prev_live_video_id="ended999",
        )
        db.commit()
    finally:
        db.close()
    assert out == {"action": "vod_not_ready", "video_id": "ended999"}
    assert _jobs(tmp_db, user_id) == []


def test_hook_offline_vod_job_created_and_deduped(tmp_db, monkeypatch):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    monkeypatch.setattr(lc, "vod_is_available", lambda vid: True)
    db = _db(tmp_db)
    try:
        ch = db.get(YTChannel, ch_id)
        out = lc.on_live_status_change(
            db, ch, {"status": "offline"},
            prev_live_video_id="ended999",
        )
        db.commit()
        out2 = lc.on_live_status_change(
            db, ch, {"status": "offline"},
            prev_live_video_id="ended999",
        )
        db.commit()
    finally:
        db.close()
    assert out["action"] == "vod_job_created"
    assert out["video_id"] == "ended999"
    assert out2 == {"action": "vod_already_queued", "video_id": "ended999"}
    jobs = _jobs(tmp_db, user_id)
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    db = _db(tmp_db)
    try:
        src = db.get(SourceVideo, jobs[0].source_id)
        assert src.source_type == "url"
        assert src.source_url == "https://www.youtube.com/watch?v=ended999"
        assert src.stored_path == ""
        user = db.query(type(src)).first()  # noqa: F841 (keeps session warm)
    finally:
        db.close()
    # One credit was charged for the VOD job.
    db = _db(tmp_db)
    try:
        from app.models import User

        assert db.get(User, user_id).credits == 69
    finally:
        db.close()


def test_hook_offline_vod_no_credits_creates_nothing(tmp_db, monkeypatch):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=0)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    monkeypatch.setattr(lc, "vod_is_available", lambda vid: True)
    db = _db(tmp_db)
    try:
        ch = db.get(YTChannel, ch_id)
        out = lc.on_live_status_change(
            db, ch, {"status": "offline"},
            prev_live_video_id="ended999",
        )
        db.commit()
    finally:
        db.close()
    assert out["action"] == "vod_failed"
    assert _jobs(tmp_db, user_id) == []


def test_hook_never_raises(tmp_db, monkeypatch):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    monkeypatch.setattr(lc, "start_live_capture",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    db = _db(tmp_db)
    try:
        ch = db.get(YTChannel, ch_id)
        out = lc.on_live_status_change(
            db, ch,
            {"status": "live", "live_video_id": "abc123", "live_title": "Live!"},
        )
    finally:
        db.close()
    assert out["action"] == "error"


# ---------------------------------------------------------------- jobs
def test_create_segment_job_queues_upload_source(tmp_db):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    db = _db(tmp_db)
    try:
        from app.models import User

        user = db.get(User, user_id)
        ch = db.get(YTChannel, ch_id)
        ch.live_video_id = "abc123"
        job = lc.create_segment_job(
            db, user, ch, Path("/tmp/seg1.mp4"), 1, "Test stream")
        db.commit()
        assert job.status == "queued"
        src = db.get(SourceVideo, job.source_id)
        assert src.source_type == "upload"
        assert src.stored_path == "/tmp/seg1.mp4"
        assert "segment 1" in src.title
        assert db.get(User, user_id).credits == 69
    finally:
        db.close()


def test_create_vod_job_dedupes(tmp_db):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    db = _db(tmp_db)
    try:
        from app.models import User

        user = db.get(User, user_id)
        ch = db.get(YTChannel, ch_id)
        job = lc.create_vod_job(db, user, ch, "vid1", "Title")
        db.commit()
        assert job is not None
        assert lc.create_vod_job(db, user, ch, "vid1", "Title") is None
        db.commit()
    finally:
        db.close()
    assert len(_jobs(tmp_db, user_id)) == 1


# ---------------------------------------------------------------- scoring
def test_score_segment_file_returns_ranked_candidates(tmp_path):
    video = _gen_video(tmp_path / "seg.mp4", seconds=25)
    out = lc.score_segment_file(str(video))
    assert out["duration_s"] == pytest.approx(25, abs=1.0)
    assert out["has_audio"] is True
    assert out["has_transcript"] is False
    cands = out["candidates"]
    assert 1 <= len(cands) <= 5
    scores = [c["score"] for c in cands]
    assert scores == sorted(scores, reverse=True)
    for c in cands:
        assert 10.0 <= c["end_s"] - c["start_s"] <= 90.0
        assert 0.0 <= c["score"] <= 100.0
        assert "reason" in c and c["reason"]


def test_score_segment_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        lc.score_segment_file(str(tmp_path / "nope.mp4"))


# ---------------------------------------------------------------- capture loop
def test_capture_loop_records_one_segment_and_queues_job(tmp_path, tmp_db, monkeypatch):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    db = _db(tmp_db)
    try:
        ch = db.get(YTChannel, ch_id)
        ch.live_video_id = "abc123"
        ch.live_title = "Test stream"
        db.commit()
    finally:
        db.close()
    seg_src = _gen_video(tmp_path / "seed.mp4", seconds=5)
    monkeypatch.setattr(lc, "SessionLocal", tmp_db)
    monkeypatch.setattr(lc, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(lc, "LIVE_DIR", tmp_path / "storage" / "live_capture")
    monkeypatch.setattr(
        lc, "resolve_live_stream_url",
        lambda url: {"is_live": True, "title": "Test stream",
                     "stream_url": "https://example.invalid/stream",
                     "height": 480, "format_id": "94"},
    )

    def fake_record(stream_url, dest_path, seconds, timeout=None):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy(seg_src, dest_path)
        return dest_path

    monkeypatch.setattr(lc, "record_segment", fake_record)
    lc._capture_loop(ch_id, user_id, "abc123", "Test stream",
                     segment_seconds=5, max_segments=1)
    jobs = _jobs(tmp_db, user_id)
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    db = _db(tmp_db)
    try:
        src = db.get(SourceVideo, jobs[0].source_id)
        assert src.source_type == "upload"
        assert Path(src.stored_path).is_file()
        assert "segment 1" in src.title
    finally:
        db.close()
    # Marker is cleaned up when the loop finishes.
    assert not list((tmp_path / "storage" / "live_capture").glob("*.running"))


def test_start_live_capture_does_not_deadlock(tmp_path, tmp_db, monkeypatch):
    """Regression: start_live_capture used to deadlock on its own lock
    (it held _running_lock while calling capture_in_progress, which
    re-acquired the same non-reentrant lock)."""
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    seg_src = _gen_video(tmp_path / "seed.mp4", seconds=5)
    monkeypatch.setattr(lc, "SessionLocal", tmp_db)
    monkeypatch.setattr(lc, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(lc, "LIVE_DIR", tmp_path / "storage" / "live_capture")
    monkeypatch.setattr(
        lc, "resolve_live_stream_url",
        lambda url: {"is_live": True, "title": "Test stream",
                     "stream_url": "https://example.invalid/stream",
                     "height": 480, "format_id": "94"},
    )

    def fake_record(stream_url, dest_path, seconds, timeout=None):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy(seg_src, dest_path)
        return dest_path

    monkeypatch.setattr(lc, "record_segment", fake_record)
    assert lc.start_live_capture(ch_id, user_id, "abc123", "Test stream",
                                 segment_seconds=5, max_segments=1) is True
    assert lc.start_live_capture(ch_id, user_id, "abc123", "Test stream") is False
    deadline = time.time() + 30
    while time.time() < deadline and lc.capture_in_progress(ch_id, "abc123"):
        time.sleep(0.2)
    assert not lc.capture_in_progress(ch_id, "abc123")
    assert len(_jobs(tmp_db, user_id)) == 1


def test_capture_loop_stops_when_stream_ends(tmp_path, tmp_db, monkeypatch):
    user_id = make_user(tmp_db, username="prouser", email="pro@example.com",
                        password=PASSWORD, plan="pro", credits=70)
    ch_id = _make_channel(tmp_db, user_id, authorized=True)
    monkeypatch.setattr(lc, "SessionLocal", tmp_db)
    monkeypatch.setattr(lc, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(lc, "LIVE_DIR", tmp_path / "storage" / "live_capture")
    monkeypatch.setattr(
        lc, "resolve_live_stream_url",
        lambda url: (_ for _ in ()).throw(lc.LiveCaptureError("not live")),
    )
    lc._capture_loop(ch_id, user_id, "abc123", "Test stream",
                     segment_seconds=5, max_segments=3)
    assert _jobs(tmp_db, user_id) == []


# ---------------------------------------------------------------- endpoint
def test_moments_endpoint_scores_finished_segment(client, tmp_db, tmp_path):
    make_user(tmp_db, username="prouser", email="pro@example.com",
              password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "prouser", PASSWORD)
    video = _gen_video(tmp_path / "seg.mp4", seconds=25)
    user_id = 1
    db = _db(tmp_db)
    try:
        from app.models import User

        user_id = db.query(User).filter(User.username == "prouser").one().id
    finally:
        db.close()
    src_id = _make_source(tmp_db, user_id, str(video))
    resp = client.get(f"/api/v1/live/segments/{src_id}/moments")
    assert resp.status_code == 200, resp.text[:500]
    data = resp.json()
    assert data["duration_s"] == pytest.approx(25, abs=1.0)
    assert 1 <= len(data["candidates"]) <= 5
    assert data["has_transcript"] is False


def test_moments_endpoint_missing_file_returns_409(client, tmp_db):
    make_user(tmp_db, username="prouser", email="pro@example.com",
              password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "prouser", PASSWORD)
    src_id = _make_source(tmp_db, 1, "/tmp/does-not-exist.mp4")
    resp = client.get(f"/api/v1/live/segments/{src_id}/moments")
    assert resp.status_code == 409


def test_moments_endpoint_free_user_forbidden(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/api/v1/live/segments/1/moments")
    assert resp.status_code == 403


def test_html_check_without_api_key_still_works_with_hook(client, tmp_db):
    """The hook must not break the existing check flow when no API key
    is configured (status 'not_configured')."""
    pro = make_user(tmp_db, username="prouser", email="pro@example.com",
                    password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "prouser", PASSWORD)
    token = get_csrf(client, "/yt")
    resp = client.post("/yt/channels",
                       data={"channel_url": "https://www.youtube.com/@testchannel",
                             "csrf_token": token})
    assert resp.status_code == 303
    db = _db(tmp_db)
    try:
        ch_id = db.query(YTChannel).filter(YTChannel.user_id == pro).one().id
    finally:
        db.close()
    token = get_csrf(client, "/yt")
    resp = client.post(f"/yt/channels/{ch_id}/check",
                       data={"csrf_token": token})
    assert resp.status_code == 200
    assert "not configured" in resp.text.lower()
