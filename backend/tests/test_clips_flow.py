"""Integration tests: clip creation, pipeline processing, approve/download,
editor re-render (trim), search isolation."""
import subprocess

import pytest

from app.config import STORAGE_DIR
from app.models import Clip, ClipJob, SourceVideo, User
from conftest import get_csrf, login_as, make_user

PASSWORD = "correct-horse-battery-staple"
NO_CREDITS_MSG = "No clip credits left — renew or upgrade your plan."
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


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


@pytest.fixture()
def patched_pipeline_session(tmp_db, monkeypatch):
    """process_job opens its own SessionLocal (the real DB engine); point
    it at the test database for this test."""
    import app.pipeline.run as run

    monkeypatch.setattr(run, "SessionLocal", tmp_db)
    return tmp_db


def _db(session_factory):
    return session_factory()


def _credits(session_factory, user_id):
    db = _db(session_factory)
    try:
        return db.get(User, user_id).credits
    finally:
        db.close()


def _job(session_factory, job_id):
    db = _db(session_factory)
    try:
        return db.get(ClipJob, job_id)
    finally:
        db.close()


def _clip(session_factory, clip_id):
    db = _db(session_factory)
    try:
        return db.get(Clip, clip_id)
    finally:
        db.close()


def _clips_for_job(session_factory, job_id):
    db = _db(session_factory)
    try:
        return db.query(Clip).filter(Clip.job_id == job_id).all()
    finally:
        db.close()


def make_test_video(path, duration=12):
    """A small real video: moving test pattern + sine audio."""
    subprocess.run(
        ["/usr/bin/ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i",
         f"testsrc2=size=640x360:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-shortest", "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True, timeout=120,
    )
    assert path.is_file() and path.stat().st_size > 0


def ffprobe_duration(path):
    out = subprocess.run(
        ["/usr/bin/ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return float(out.stdout.strip())


def create_url_job(client, title="URL clip", source_url=YOUTUBE_URL,
                   caption_style="simple", export_height="1280",
                   facecam_mode="auto", token_url="/clips/new"):
    # CSRF tokens are per-session, not per-page: callers whose form page
    # renders no form (e.g. a zero-credit user on /clips/new) may fetch
    # the token from any other page that renders one.
    token = get_csrf(client, token_url)
    return client.post(
        "/clips/new",
        data={"title": title, "caption_style": caption_style,
              "export_height": export_height, "facecam_mode": facecam_mode,
              "source_url": source_url, "csrf_token": token},
    )


def create_upload_job(client, video_path, title="Upload clip"):
    token = get_csrf(client, "/clips/new")
    return client.post(
        "/clips/new",
        data={"title": title, "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "csrf_token": token},
        files={"video": ("test.mp4", video_path.read_bytes(), "video/mp4")},
    )


def _job_id_from(resp):
    assert resp.status_code == 303, resp.text[:500]
    return int(resp.headers["location"].rsplit("/", 1)[-1])


def insert_clip_row(session_factory, user_id, title):
    """Insert a candidate Clip (+ source + job) directly in the DB."""
    db = _db(session_factory)
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
                    status="candidate", qc_passed=True, file_path="")
        db.add(clip)
        db.commit()
        return clip.id
    finally:
        db.close()


# ------------------------------------------------------------------ creation
def test_create_clip_from_youtube_url_deducts_one_credit(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = create_url_job(client)
    job_id = _job_id_from(resp)
    assert resp.headers["location"] == f"/clips/jobs/{job_id}"
    job = _job(tmp_db, job_id)
    assert job is not None and job.status == "queued" and job.user_id == alice
    assert _credits(tmp_db, alice) == 4


def test_create_clip_bad_source_host_returns_400(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = create_url_job(client, source_url="https://example.com/video.mp4")
    assert resp.status_code == 400
    assert "not supported" in resp.text


def test_free_user_neon_caption_style_returns_400(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = create_url_job(client, caption_style="neon")
    assert resp.status_code == 400
    assert "not available on your plan" in resp.text


def test_create_clip_with_file_upload_deducts_credit(client, tmp_db, tmp_path):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video)
    resp = create_upload_job(client, video)
    job_id = _job_id_from(resp)
    assert _credits(tmp_db, alice) == 4
    db = _db(tmp_db)
    try:
        job = db.get(ClipJob, job_id)
        source = db.get(SourceVideo, job.source_id)
        assert source.source_type == "upload"
        assert source.stored_path
        from pathlib import Path
        assert Path(source.stored_path).is_file()
    finally:
        db.close()


def test_zero_credit_user_cannot_create_clip(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD, credits=0)
    login_as(client, tmp_db, "alice", PASSWORD)
    # /clips/new renders no form for a zero-credit user (blocked banner
    # only), and /dashboard has no form at all, so the CSRF token is
    # fetched from the login page (tokens are per-session, not per-page).
    resp = create_url_job(client, token_url="/login")
    assert resp.status_code == 400
    assert NO_CREDITS_MSG in resp.text


# ------------------------------------------------------------------ pipeline
def test_process_job_on_real_video_reaches_done_with_qc_passed_clip(
        client, tmp_db, tmp_path, patched_pipeline_session):
    from app.pipeline.run import process_job

    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    job_id = _job_id_from(create_upload_job(client, video))
    process_job(job_id)
    job = _job(tmp_db, job_id)
    assert job.status == "done", f"job failed: {job.error} | log: {job.log[-500:]}"
    assert job.progress == 100
    clips = _clips_for_job(tmp_db, job_id)
    assert len(clips) >= 1
    passed = [c for c in clips if c.qc_passed]
    assert len(passed) >= 1
    clip = passed[0]
    assert clip.file_path
    from pathlib import Path
    assert Path(clip.file_path).is_file()
    assert Path(clip.file_path).stat().st_size > 0
    # No transcript exists in this VM (faster-whisper not installed), so the
    # job honestly records that instead of inventing captions.
    assert "Transcription model not installed" in job.log


def test_approve_clip_then_download_increments_counter(
        client, tmp_db, tmp_path, patched_pipeline_session):
    from app.pipeline.run import process_job

    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    job_id = _job_id_from(create_upload_job(client, video))
    process_job(job_id)
    clips = _clips_for_job(tmp_db, job_id)
    assert clips, "no clips produced"
    clip_id = clips[0].id
    token = get_csrf(client, f"/clips/{clip_id}")
    resp = client.post(f"/clips/{clip_id}/approve", data={"csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/downloads"
    assert _clip(tmp_db, clip_id).status == "approved"
    dl = client.get(f"/downloads/{clip_id}")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("video/mp4")
    assert len(dl.content) > 0
    assert _clip(tmp_db, clip_id).downloads == 1


def test_download_unapproved_clip_returns_403(
        client, tmp_db, tmp_path, patched_pipeline_session):
    from app.pipeline.run import process_job

    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    job_id = _job_id_from(create_upload_job(client, video))
    process_job(job_id)
    clips = _clips_for_job(tmp_db, job_id)
    assert clips
    clip_id = clips[0].id
    assert _clip(tmp_db, clip_id).status == "candidate"
    assert client.get(f"/downloads/{clip_id}").status_code == 403


def test_job_page_forbidden_for_other_user(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    make_user(tmp_db, username="bob", email="bob@example.com", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    job_id = _job_id_from(create_url_job(client))
    login_as(client, tmp_db, "bob", PASSWORD)
    assert client.get(f"/clips/jobs/{job_id}").status_code == 403


def test_editor_rerender_trim_produces_trim_length_clip(
        client, tmp_db, tmp_path, patched_pipeline_session):
    from app.pipeline.run import process_job

    lite = make_user(tmp_db, username="liteuser", email="lite@example.com",
                     password=PASSWORD, plan="lite", credits=20)
    login_as(client, tmp_db, "liteuser", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    job_id = _job_id_from(create_upload_job(client, video))
    process_job(job_id)
    clips = _clips_for_job(tmp_db, job_id)
    assert clips
    clip_id = clips[0].id
    token = get_csrf(client, f"/clips/{clip_id}")
    resp = client.post(
        f"/clips/{clip_id}/edit",
        data={"start_s": "2", "end_s": "5", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "csrf_token": token},
    )
    new_job_id = _job_id_from(resp)
    assert new_job_id != job_id
    new_job = _job(tmp_db, new_job_id)
    assert new_job.trim_start_s == 2.0
    assert new_job.trim_end_s == 5.0
    process_job(new_job_id)
    new_job = _job(tmp_db, new_job_id)
    assert new_job.status == "done", f"re-render failed: {new_job.error}"
    new_clips = _clips_for_job(tmp_db, new_job_id)
    assert len(new_clips) == 1
    assert abs(new_clips[0].duration_s - 3.0) <= 0.6
    from pathlib import Path
    assert Path(new_clips[0].file_path).is_file()
    assert abs(ffprobe_duration(new_clips[0].file_path) - 3.0) <= 0.6
    assert _credits(tmp_db, lite) == 18  # 1 for the original job + 1 for the re-render


def test_free_user_edit_returns_400(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    clip_id = insert_clip_row(tmp_db, alice, "Free clip")
    token = get_csrf(client, f"/clips/{clip_id}")
    resp = client.post(
        f"/clips/{clip_id}/edit",
        data={"start_s": "1", "end_s": "3", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "csrf_token": token},
    )
    assert resp.status_code == 400
    assert "Manual editing requires the Lite or Pro plan" in resp.text


def test_search_returns_only_own_clips(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    bob = make_user(tmp_db, username="bob", email="bob@example.com",
                    password=PASSWORD)
    insert_clip_row(tmp_db, alice, "Alice Sunset Vlog")
    insert_clip_row(tmp_db, bob, "Bob Sunset Vlog")
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/search?q=Sunset")
    assert resp.status_code == 200
    assert "Alice Sunset Vlog" in resp.text
    assert "Bob Sunset Vlog" not in resp.text
