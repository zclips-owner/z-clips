"""Tests: YIZERGE-style editor features (aspect ratio, framing layout,
caption typography, sticker, social-post card, yellow karaoke) and the
Lite/Pro entitlement for manual editing + manual facecam.

Covers app/editor_fields.py validation, app/pipeline/render.py geometry
and ASS output, the HTML + API create/edit endpoints, and the Alembic
migration for the new clip_jobs columns.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from app.config import STORAGE_DIR
from app.editor_fields import parse_editor_fields
from app.models import Clip, ClipJob, SourceVideo, User
from app.pipeline.render import output_dims, write_ass_file
from conftest import get_csrf, login_as, make_user

PASSWORD = "correct-horse-battery-staple"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_storage():
    """Remove any files the test writes under the real storage dir."""
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


def _db(session_factory):
    return session_factory()


def _job(session_factory, job_id):
    db = _db(session_factory)
    try:
        return db.get(ClipJob, job_id)
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


def ffprobe_dims(path):
    out = subprocess.run(
        ["/usr/bin/ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries",
         "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(data["format"]["duration"])


def _insert_render_rows(session_factory, user_id, video_path, **job_kwargs):
    """Insert source + job + clip rows for a direct render_clip call."""
    db = _db(session_factory)
    src = SourceVideo(user_id=user_id, title="render test", source_type="upload",
                      stored_path=str(video_path), status="uploaded",
                      duration_s=12.0, width=640, height=360, has_audio=True)
    db.add(src)
    db.flush()
    job_kwargs = {"facecam_mode": "auto", "caption_style": "simple",
                  "export_height": 1280, **job_kwargs}
    job = ClipJob(user_id=user_id, source_id=src.id, title="render test",
                  status="rendering", progress=40, **job_kwargs)
    db.add(job)
    db.flush()
    clip = Clip(job_id=job.id, user_id=user_id, source_id=src.id,
                title="render test", start_s=1.0, end_s=4.0, duration_s=3.0,
                status="candidate")
    db.add(clip)
    db.commit()
    return db, src, job, clip


# ------------------------------------------------------------------ validation
def test_parse_editor_fields_defaults():
    fields, error = parse_editor_fields({}, manual_editing=False)
    assert error is None
    assert fields == {
        "aspect_ratio": "9:16", "layout": "standard",
        "caption_size": "medium", "caption_position": "bottom",
        "caption_bg": "outline", "sticker_text": "",
        "post_author": "", "post_text": "",
    }


@pytest.mark.parametrize("raw", [
    {"aspect_ratio": "2:1"},
    {"layout": "quad"},
    {"caption_size": "huge"},
    {"caption_position": "left"},
    {"caption_bg": "glow"},
    {"sticker_text": "x" * 41},
    {"post_author": "x" * 41},
    {"post_text": "x" * 141},
])
def test_parse_editor_fields_rejects_bad_values(raw):
    _, error = parse_editor_fields(raw, manual_editing=True)
    assert error is not None


def test_parse_editor_fields_free_cannot_use_editor_features():
    _, error = parse_editor_fields({"aspect_ratio": "1:1"}, manual_editing=False)
    assert error is not None and "Lite or Pro" in error
    _, error = parse_editor_fields({"sticker_text": "Hi"}, manual_editing=False)
    assert error is not None and "Lite or Pro" in error
    fields, error = parse_editor_fields({"aspect_ratio": "1:1"}, manual_editing=True)
    assert error is None and fields["aspect_ratio"] == "1:1"


# ------------------------------------------------------------------ geometry
@pytest.mark.parametrize("aspect,height,expected", [
    ("9:16", 1280, (720, 1280)),
    ("1:1", 1280, (1280, 1280)),
    ("4:3", 1280, (1706, 1280)),
    ("16:9", 1280, (2276, 1280)),
    ("16:9", 1920, (3412, 1920)),
    ("9:16", 3840, (2160, 3840)),
])
def test_output_dims(aspect, height, expected):
    assert output_dims(aspect, height) == expected


def test_output_dims_unknown_aspect_falls_back_to_9_16():
    assert output_dims("bogus", 1280) == (720, 1280)


# ------------------------------------------------------------------ ASS output
WORDS = [
    {"start": 0.0, "end": 0.4, "word": "Hello"},
    {"start": 0.4, "end": 0.9, "word": "world"},
    {"start": 0.9, "end": 1.5, "word": "today."},
]


def test_write_ass_karaoke_highlight_is_yellow(tmp_path):
    path = tmp_path / "k.ass"
    write_ass_file(path, WORDS, 0.0, 2.0, "karaoke", 1280)
    text = path.read_text()
    # Karaoke style line: SecondaryColour is the current-word highlight.
    assert "&H0000FFFF" in text  # yellow (AABBGGRR)
    assert "{\\k40}Hello" in text and "{\\k50}world" in text


def test_write_ass_caption_typography(tmp_path):
    small = tmp_path / "s.ass"
    write_ass_file(small, WORDS, 0.0, 2.0, "simple", 1280,
                   caption_size="small", caption_position="bottom", caption_bg="outline")
    large_top_box = tmp_path / "l.ass"
    write_ass_file(large_top_box, WORDS, 0.0, 2.0, "simple", 1280,
                   caption_size="large", caption_position="top", caption_bg="box")
    middle_none = tmp_path / "m.ass"
    write_ass_file(middle_none, WORDS, 0.0, 2.0, "simple", 1280,
                   caption_size="medium", caption_position="middle", caption_bg="none")
    small_text = small.read_text()
    large_text = large_top_box.read_text()
    middle_text = middle_none.read_text()
    # Large font is bigger than small font (52 * 0.75 = 39 vs 52 * 1.35 = 70).
    assert ",39," in small_text
    assert ",70," in large_text
    # Top position uses Alignment 5, middle uses 8, bottom uses 2.
    assert ",5,20,20," in large_text
    assert ",8,20,20,0,1" in middle_text
    assert ",2,20,20," in small_text
    # Box background uses BorderStyle 3; outline/none use BorderStyle 1.
    # (simple style: Outline 2, Shadow 0.)
    assert ",3,0,0,5,20,20," in large_text
    assert ",1,2,0,2,20,20," in small_text


def test_write_ass_sticker_and_postcard(tmp_path):
    path = tmp_path / "o.ass"
    write_ass_file(path, WORDS, 0.0, 2.0, "simple", 1280,
                   sticker_text="Which one?", post_author="@someone",
                   post_text="Vote in the comments!")
    text = path.read_text()
    assert "Style: sticker," in text and "Which one?" in text
    assert "Style: postcard," in text
    assert "{\\b1}@someone{\\b0}\\NVote in the comments!" in text
    # Sticker spans the whole clip (layer 1, 0 -> duration).
    assert "Dialogue: 1,0:00:00.00,0:00:02.00,sticker,," in text


def test_write_ass_postcard_needs_both_author_and_text(tmp_path):
    path = tmp_path / "half.ass"
    write_ass_file(path, WORDS, 0.0, 2.0, "simple", 1280,
                   post_author="@someone", post_text="")
    text = path.read_text()
    assert "postcard,," not in text


def test_write_ass_playres_matches_aspect(tmp_path):
    path = tmp_path / "w.ass"
    write_ass_file(path, WORDS, 0.0, 2.0, "simple", 1280, width=2276)
    text = path.read_text()
    assert "PlayResX: 2276" in text and "PlayResY: 1280" in text


# ------------------------------------------------------------------ entitlements
def test_lite_user_can_create_with_editor_fields(client, tmp_db, tmp_path):
    lite = make_user(tmp_db, username="liteuser", email="lite@example.com",
                     password=PASSWORD, plan="lite", credits=5)
    login_as(client, tmp_db, "liteuser", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    token = get_csrf(client, "/clips/new")
    resp = client.post(
        "/clips/new",
        data={"title": "Lite edit", "caption_style": "karaoke",
              "export_height": "1280", "facecam_mode": "auto",
              "aspect_ratio": "1:1", "layout": "standard",
              "caption_size": "large", "caption_position": "top",
              "caption_bg": "box", "sticker_text": "Which one?",
              "post_author": "@someone", "post_text": "Vote!",
              "csrf_token": token},
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
    )
    assert resp.status_code == 303, resp.text[:500]
    job_id = int(resp.headers["location"].rsplit("/", 1)[-1])
    job = _job(tmp_db, job_id)
    assert job.aspect_ratio == "1:1"
    assert job.caption_size == "large"
    assert job.caption_position == "top"
    assert job.caption_bg == "box"
    assert job.sticker_text == "Which one?"
    assert job.post_author == "@someone"
    assert job.post_text == "Vote!"
    db = _db(tmp_db)
    try:
        assert db.get(User, lite).credits == 4
    finally:
        db.close()


def test_free_user_editor_fields_rejected(client, tmp_db, tmp_path):
    make_user(tmp_db, username="alice", password=PASSWORD, plan="free", credits=5)
    login_as(client, tmp_db, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    token = get_csrf(client, "/clips/new")
    resp = client.post(
        "/clips/new",
        data={"title": "Free edit", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "aspect_ratio": "1:1", "csrf_token": token},
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
    )
    assert resp.status_code == 400
    assert "Lite or Pro" in resp.text


def test_free_user_manual_facecam_rejected(client, tmp_db, tmp_path):
    make_user(tmp_db, username="alice", password=PASSWORD, plan="free", credits=5)
    login_as(client, tmp_db, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    token = get_csrf(client, "/clips/new")
    resp = client.post(
        "/clips/new",
        data={"title": "Free facecam", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "manual",
              "csrf_token": token},
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
    )
    assert resp.status_code == 400
    assert "Lite or Pro" in resp.text


def test_lite_user_manual_facecam_allowed_via_edit(client, tmp_db, tmp_path, monkeypatch):
    from app.pipeline.run import process_job
    import app.pipeline.run as run

    monkeypatch.setattr(run, "SessionLocal", tmp_db)
    make_user(tmp_db, username="liteuser", email="lite@example.com",
              password=PASSWORD, plan="lite", credits=5)
    login_as(client, tmp_db, "liteuser", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    token = get_csrf(client, "/clips/new")
    resp = client.post(
        "/clips/new",
        data={"title": "Lite base", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "csrf_token": token},
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
    )
    job_id = int(resp.headers["location"].rsplit("/", 1)[-1])
    process_job(job_id)
    db = _db(tmp_db)
    try:
        clip = db.query(Clip).filter(Clip.job_id == job_id).first()
        clip_id = clip.id
    finally:
        db.close()
    token = get_csrf(client, f"/clips/{clip_id}")
    resp = client.post(
        f"/clips/{clip_id}/edit",
        data={"start_s": "1", "end_s": "4", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "manual",
              "facecam_x": "0.6", "facecam_y": "0.05",
              "facecam_w": "0.3", "facecam_h": "0.3",
              "layout": "facecam_top", "csrf_token": token},
    )
    assert resp.status_code == 303, resp.text[:500]
    new_job_id = int(resp.headers["location"].rsplit("/", 1)[-1])
    new_job = _job(tmp_db, new_job_id)
    assert new_job.facecam_mode == "manual"
    assert new_job.facecam_x == 0.6
    assert new_job.layout == "facecam_top"


def test_lite_user_bad_aspect_ratio_rejected(client, tmp_db, tmp_path):
    make_user(tmp_db, username="liteuser", email="lite@example.com",
              password=PASSWORD, plan="lite", credits=5)
    login_as(client, tmp_db, "liteuser", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    token = get_csrf(client, "/clips/new")
    resp = client.post(
        "/clips/new",
        data={"title": "Bad aspect", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "aspect_ratio": "2:1", "csrf_token": token},
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
    )
    assert resp.status_code == 400
    assert "aspect ratio" in resp.text.lower()


# ------------------------------------------------------------------ rendering
def _render_direct(session_factory, tmp_path, **job_kwargs):
    from app.pipeline.render import render_clip

    user_id = make_user(session_factory, username="rend", email="r@example.com",
                        password=PASSWORD, plan="lite", credits=5)
    video = tmp_path / "src.mp4"
    make_test_video(video, duration=12)
    db, src, job, clip = _insert_render_rows(
        session_factory, user_id, video, **job_kwargs)
    try:
        result = render_clip(db, clip, job, src)
        job_log = job.log or ""
    finally:
        db.close()
    return result, job_log


def test_render_1_1_karaoke_with_transcript(client, tmp_db, tmp_path):
    """1:1 + karaoke end-to-end: a real transcript file makes render_clip
    burn word-by-word karaoke captions into a square clip."""
    from app.pipeline.analyze import transcript_path
    from app.pipeline.render import render_clip
    from app.storage import user_clip_dir

    user_id = make_user(tmp_db, username="rend", email="r@example.com",
                        password=PASSWORD, plan="lite", credits=5)
    video = tmp_path / "src.mp4"
    make_test_video(video, duration=12)
    db, src, job, clip = _insert_render_rows(
        tmp_db, user_id, video, aspect_ratio="1:1", caption_style="karaoke")
    try:
        user_clip_dir(user_id).mkdir(parents=True, exist_ok=True)
        transcript_path(job).write_text(json.dumps({"words": [
            {"start": 1.0, "end": 1.4, "word": "Hello"},
            {"start": 1.4, "end": 1.9, "word": "world"},
            {"start": 1.9, "end": 2.5, "word": "today."},
        ]}), encoding="utf-8")
        result = render_clip(db, clip, job, src)
    finally:
        db.close()
    w, h, dur = ffprobe_dims(result["file_path"])
    assert (w, h) == (1280, 1280) and dur > 0
    ass_text = Path(result["caption_path"]).read_text()
    assert "&H0000FFFF" in ass_text  # yellow current-word highlight
    assert "{\\k40}Hello" in ass_text


def test_render_1_1_aspect_with_sticker(client, tmp_db, tmp_path):
    result, _ = _render_direct(tmp_db, tmp_path, aspect_ratio="1:1",
                               sticker_text="Which one?")
    assert result["width"] == 1280 and result["height"] == 1280
    w, h, dur = ffprobe_dims(result["file_path"])
    assert (w, h) == (1280, 1280) and dur > 0
    assert Path(result["caption_path"]).is_file()  # sticker needs an ASS file


def test_render_16_9_with_sticker(client, tmp_db, tmp_path):
    result, _ = _render_direct(tmp_db, tmp_path, aspect_ratio="16:9",
                               sticker_text="Which one?")
    assert result["width"] == 2276 and result["height"] == 1280
    w, h, dur = ffprobe_dims(result["file_path"])
    assert (w, h) == (2276, 1280) and dur > 0


def test_render_16_9_with_postcard(client, tmp_db, tmp_path):
    result, _ = _render_direct(tmp_db, tmp_path, aspect_ratio="16:9",
                               post_author="@someone", post_text="Vote!")
    assert result["width"] == 2276 and result["height"] == 1280
    w, h, dur = ffprobe_dims(result["file_path"])
    assert (w, h) == (2276, 1280) and dur > 0


def test_render_facecam_top_layout(client, tmp_db, tmp_path):
    result, _ = _render_direct(
        tmp_db, tmp_path, facecam_mode="manual", layout="facecam_top",
        facecam_x=0.6, facecam_y=0.05, facecam_w=0.3, facecam_h=0.3)
    assert result["width"] == 720 and result["height"] == 1280
    w, h, dur = ffprobe_dims(result["file_path"])
    assert (w, h) == (720, 1280) and dur > 0


def test_render_facecam_side_layout(client, tmp_db, tmp_path):
    result, _ = _render_direct(
        tmp_db, tmp_path, facecam_mode="manual", layout="facecam_side",
        facecam_x=0.1, facecam_y=0.1, facecam_w=0.4, facecam_h=0.4)
    w, h, dur = ffprobe_dims(result["file_path"])
    assert (w, h) == (720, 1280) and dur > 0


def test_render_layout_falls_back_without_facecam_region(client, tmp_db, tmp_path):
    result, job_log = _render_direct(tmp_db, tmp_path, facecam_mode="auto",
                                     layout="facecam_top")
    w, h, dur = ffprobe_dims(result["file_path"])
    assert (w, h) == (720, 1280) and dur > 0
    assert "falling back to the standard layout" in job_log


def test_render_half_postcard_is_ignored(client, tmp_db, tmp_path):
    result, job_log = _render_direct(tmp_db, tmp_path, post_author="@someone")
    w, h, dur = ffprobe_dims(result["file_path"])
    assert dur > 0
    assert "needs both an author and text" in job_log
    assert result["caption_path"] == ""  # no transcript, no sticker, no card


# ------------------------------------------------------------------ JSON API
def _api_csrf(client):
    resp = client.get("/api/v1/csrf")
    assert resp.status_code == 200, resp.text[:300]
    return resp.json()["csrf_token"]


def test_api_edit_accepts_editor_fields_for_lite(client, tmp_db, tmp_path, monkeypatch):
    """The PWA posts JSON to /api/v1/clips/{id}/edit — Lite must be able to
    use every new editor field + manual facecam through it."""
    from app.pipeline.run import process_job
    import app.pipeline.run as run

    monkeypatch.setattr(run, "SessionLocal", tmp_db)
    make_user(tmp_db, username="liteuser", email="lite@example.com",
              password=PASSWORD, plan="lite", credits=5)
    login_as(client, tmp_db, "liteuser", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    token = get_csrf(client, "/clips/new")
    resp = client.post(
        "/clips/new",
        data={"title": "Lite base", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "csrf_token": token},
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
    )
    job_id = int(resp.headers["location"].rsplit("/", 1)[-1])
    process_job(job_id)
    db = _db(tmp_db)
    try:
        clip = db.query(Clip).filter(Clip.job_id == job_id).first()
        clip_id = clip.id
    finally:
        db.close()
    api_token = _api_csrf(client)
    resp = client.post(
        f"/api/v1/clips/{clip_id}/edit",
        json={"start_s": 1, "end_s": 4, "caption_style": "karaoke",
              "export_height": 1280, "facecam_mode": "manual",
              "facecam_x": 0.6, "facecam_y": 0.05, "facecam_w": 0.3,
              "facecam_h": 0.3, "aspect_ratio": "16:9",
              "layout": "facecam_top", "caption_size": "large",
              "caption_position": "top", "caption_bg": "box",
              "sticker_text": "Which one?", "post_author": "@someone",
              "post_text": "Vote!"},
        headers={"X-CSRF-Token": api_token},
    )
    assert resp.status_code == 201, resp.text[:500]
    job = resp.json()["job"]
    assert job["aspect_ratio"] == "16:9"
    assert job["layout"] == "facecam_top"
    assert job["caption_size"] == "large"
    assert job["caption_position"] == "top"
    assert job["caption_bg"] == "box"
    assert job["sticker_text"] == "Which one?"
    assert job["post_author"] == "@someone"
    assert job["post_text"] == "Vote!"
    # The detail endpoint exposes the new fields + option lists.
    detail = client.get(f"/api/v1/clips/{clip_id}")
    assert detail.status_code == 200
    assert detail.json()["aspect_ratios"] == ["9:16", "1:1", "4:3", "16:9"]
    assert detail.json()["layouts"] == ["standard", "facecam_top", "facecam_side"]


def test_api_create_free_user_editor_fields_rejected(client, tmp_db, tmp_path):
    make_user(tmp_db, username="alice", password=PASSWORD, plan="free", credits=5)
    login_as(client, tmp_db, "alice", PASSWORD)
    video = tmp_path / "test.mp4"
    make_test_video(video, duration=12)
    api_token = _api_csrf(client)
    resp = client.post(
        "/api/v1/clips",
        data={"title": "Free API", "caption_style": "simple",
              "export_height": "1280", "facecam_mode": "auto",
              "aspect_ratio": "1:1"},
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
        headers={"X-CSRF-Token": api_token},
    )
    assert resp.status_code == 400
    assert "Lite or Pro" in resp.text


# ------------------------------------------------------------------ migration
def test_alembic_upgrade_head_on_fresh_db(tmp_path):
    """Fresh sqlite DB -> alembic upgrade head -> all 8 editor columns exist."""
    db_file = tmp_path / "mig.db"
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_file}"
    env["APP_ENV"] = "development"
    env.setdefault("SESSION_SECRET", "test-secret-that-is-long-enough-0123456789")
    env.setdefault("OWNER_SETUP_TOKEN", "test-owner-token-that-is-long-enough-0123456789")
    proc = subprocess.run(
        [str(PROJECT_ROOT / ".venv" / "bin" / "alembic"), "upgrade", "head"],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    import sqlite3

    conn = sqlite3.connect(db_file)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(clip_jobs)")}
    finally:
        conn.close()
    for col in ("aspect_ratio", "layout", "caption_size", "caption_position",
                "caption_bg", "sticker_text", "post_author", "post_text"):
        assert col in cols
    conn = sqlite3.connect(db_file)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()
    assert version == "a3f7c91d2e84"
