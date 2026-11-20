"""Live-stream clip extraction for Z CLIPS monitoring.

Two paths, both built on the existing pipeline — nothing here re-implements
downloading, analysis, rendering or the worker:

1. LIVE CAPTURE (best effort): when a monitored channel goes live, resolve
   the live stream URL with yt-dlp and record it with ffmpeg (stream copy,
   no re-encode) in fixed-length segments. Each finished segment becomes a
   ``SourceVideo`` + queued ``ClipJob``; the existing worker picks the job
   up and ``process_job`` analyses/renders clips from it, exactly as for
   any other source.
2. VOD FALLBACK (reliable): when the stream has ended, the finished VOD is
   fetched through the existing URL-download path (``download_source_url``,
   driven by ``process_job`` for ``source_type="url"`` jobs) and the
   pipeline runs on it. No new download code lives here.

Authorisation rule (master prompt): capture/VOD jobs are created ONLY for
a channel whose ``ingestion_state`` is ``"authorized"`` — i.e. the user
recorded that the source is authorised. Monitoring alone never captures
anything, and this module never touches an arbitrary public stream.

Honesty / limits, stated plainly:
- YouTube often blocks server-side stream capture from datacenter IPs,
  and the production container is 256 MB RAM. Live capture is therefore
  best-effort: every failure is recorded on the activity feed and the
  VOD fallback is what actually delivers clips.
- There is no scheduler in the app today: live checks happen when the
  user (or the API) calls the check endpoint. ``on_live_status_change``
  is the one function the check path calls; a future worker pass would
  call the same function on a timer.
- Capture runs in a daemon thread of the web process (one segment loop
  per live video id). If the web process restarts, capture stops; the
  VOD fallback still covers the stream. Segment jobs are only queued —
  ``process_job`` is never called from here; the worker owns it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..activity import log_activity
from ..config import STORAGE_DIR
from ..database import SessionLocal
from ..models import ClipJob, CreditLedger, SourceVideo, User, YTChannel
from ..pipeline.analyze import (
    _candidate_windows,
    _score_candidate,
    audio_energy_per_second,
    detect_scene_cuts,
    probe_source,
)

FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

#: Default segment length for live capture (10 minutes). Shorter segments
#: mean smaller files and faster first clips; longer segments mean fewer
#: jobs. A 10-minute 480p segment is roughly 90-180 MB on disk.
SEGMENT_SECONDS = int(os.getenv("LIVE_SEGMENT_SECONDS", "600"))
#: Safety cap on consecutive segments per live stream (2 h at 10 min).
MAX_SEGMENTS_PER_STREAM = int(os.getenv("LIVE_MAX_SEGMENTS", "12"))
#: Highest combined (video+audio) format height captured. 480p keeps
#: segment files and the analysis pass small; clips are rendered
#: vertical 720x1280 from it by the existing pipeline.
CAPTURE_MAX_HEIGHT = int(os.getenv("LIVE_CAPTURE_MAX_HEIGHT", "480"))

LIVE_DIR = STORAGE_DIR / "live_capture"


class LiveCaptureError(RuntimeError):
    """Live capture / VOD handling failed (blocked, not live, no credits...)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _short_error(message: str) -> str:
    text = " ".join((message or "").strip().split())
    return text[:300] or "live capture failed"


def _is_unlimited(user: User) -> bool:
    """Owner / complimentary-premium users have unlimited credits."""
    return user.role == "owner" or bool(user.complimentary_premium)


def _deduct_credit(db, user: User, job_id: int) -> None:
    """Decrement 1 credit + ledger entry.

    Mirrors ``routers/customer._deduct_credit`` (kept local so the service
    layer never imports a router). Unlimited users are not charged.
    Callers commit once after creating the job/activity rows.
    """
    if _is_unlimited(user):
        return
    user.credits -= 1
    user.clips_used += 1
    db.add(CreditLedger(
        user_id=user.id, delta=-1, reason="Clip job created",
        ref_type="clip_job", ref_id=job_id,
    ))


def _ydl_extract_opts() -> dict:
    """yt-dlp options for metadata-only extraction (no download).

    Same player clients and certificate handling as
    ``pipeline/download._ydl_options``: the Android player client is the
    usual way a plain fetch still works, and when the environment
    configures a CA bundle (sandbox egress proxy) yt-dlp is told to use
    the system store instead of its bundled certifi copy — TLS
    verification is never disabled.
    """
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "no_color": True,
        "socket_timeout": 30,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        opts["compat_opts"] = ["no-certifi"]
    return opts


def _extract_info_bounded(url: str, timeout: float) -> dict:
    """``_extract_info`` with a hard wall-clock timeout.

    yt-dlp's ``socket_timeout`` is per socket operation, and a fetch can
    still stall for minutes (observed once on this VM: a metadata fetch
    hung ~7 min with ~1s CPU). The VOD probe runs inside a check HTTP
    request, so it must never stall the response; the capture loop runs
    in a background thread, but a hung resolve would wedge the loop.
    Runs the fetch on a daemon thread and gives up after *timeout*.
    """
    result: dict = {}

    def _run() -> None:
        try:
            result["info"] = _extract_info(url)
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller
            result["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise LiveCaptureError(
            f"YouTube metadata fetch timed out after {timeout:g}s (network stall)."
        )
    if "error" in result:
        raise result["error"]
    return result.get("info", {})


def _extract_info(url: str) -> dict:
    """yt-dlp extract_info(download=False), or raise LiveCaptureError."""
    try:
        import yt_dlp  # noqa: PLC0415  (only the live path needs it)
    except ImportError as exc:
        raise LiveCaptureError(
            "Live capture is unavailable — the video downloader (yt-dlp) "
            "is not installed on the server."
        ) from exc
    try:
        with yt_dlp.YoutubeDL(_ydl_extract_opts()) as ydl:
            return ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        raise LiveCaptureError(
            f"YouTube did not return stream information: {_short_error(str(exc))}"
        ) from exc


# ---------------------------------------------------------------------------
# Live stream resolution + recording
# ---------------------------------------------------------------------------
def vod_url_for(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def resolve_live_stream_url(video_url: str) -> dict:
    """Resolve a live video URL to a directly recordable stream URL.

    Returns {"is_live", "title", "stream_url", "height", "format_id"}.
    Raises :class:`LiveCaptureError` when the video is not live, has no
    recordable combined format, or YouTube blocks the fetch.
    """
    info = _extract_info_bounded(video_url, timeout=120)
    if not info.get("is_live"):
        raise LiveCaptureError(
            f"This video is not live right now (status: {info.get('live_status') or 'unknown'})."
        )
    combined = [
        f for f in (info.get("formats") or [])
        if f.get("url") and f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
    ]
    if not combined:
        raise LiveCaptureError(
            "This live stream has no combined video+audio format to record."
        )
    # Highest combined format at or below the capture cap (480p default);
    # fall back to the lowest combined format when nothing fits the cap.
    fitting = [f for f in combined if (f.get("height") or 0) <= CAPTURE_MAX_HEIGHT]
    chosen = max(fitting or combined, key=lambda f: f.get("height") or 0)
    return {
        "is_live": True,
        "title": str(info.get("title") or ""),
        "stream_url": str(chosen["url"]),
        "height": int(chosen.get("height") or 0),
        "format_id": str(chosen.get("format_id") or ""),
    }


def record_segment(stream_url: str, dest_path: Path, seconds: int,
                   timeout: int | None = None) -> Path:
    """Record *seconds* of a live stream URL to *dest_path* (stream copy).

    No re-encode: ffmpeg copies the stream as-is, so this is cheap in
    CPU and RAM (measured ~68 MB peak RSS for a 720p HLS stream).
    Raises :class:`LiveCaptureError` on ffmpeg failure or timeout.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if timeout is None:
        timeout = int(seconds) + 180
    try:
        result = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-i", stream_url,
             "-t", str(int(seconds)), "-c", "copy", str(dest_path)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LiveCaptureError(
            f"Recording timed out after {timeout}s."
        ) from exc
    if result.returncode != 0:
        raise LiveCaptureError(
            f"Recording failed: {_short_error(result.stderr)}"
        )
    if not dest_path.is_file() or dest_path.stat().st_size <= 0:
        raise LiveCaptureError("Recording produced no video file.")
    return dest_path


def capture_live_segment(video_url: str, dest_dir: Path,
                         segment_seconds: int = SEGMENT_SECONDS) -> Path:
    """Resolve + record one live segment. Returns the segment file path.

    Raises :class:`LiveCaptureError` when the stream cannot be resolved
    or recorded (e.g. YouTube blocking server-side capture).
    """
    resolved = resolve_live_stream_url(video_url)
    out = Path(dest_dir) / f"live_seg_{uuid.uuid4().hex}.mp4"
    return record_segment(resolved["stream_url"], out, segment_seconds)


# ---------------------------------------------------------------------------
# Job creation (SourceVideo + ClipJob, queued for the existing worker)
# ---------------------------------------------------------------------------
def _create_job(db, user: User, *, title: str, source_type: str,
                stored_path: str, source_url: str,
                original_filename: str = "") -> ClipJob:
    """Create a SourceVideo + queued ClipJob, charging 1 credit.

    Raises :class:`LiveCaptureError` when the user has no credits left
    (unlimited users are never charged). The caller commits.
    """
    if not _is_unlimited(user) and user.credits < 1:
        raise LiveCaptureError(
            "No clip credits left — the live segment was kept on disk, "
            "but no clip job was created. Top up credits to clip it."
        )
    source = SourceVideo(
        user_id=user.id,
        title=title,
        source_type=source_type,
        original_filename=original_filename,
        stored_path=stored_path,
        source_url=source_url,
        status="uploaded",
    )
    db.add(source)
    db.flush()
    job = ClipJob(
        user_id=user.id,
        source_id=source.id,
        title=title,
        status="queued",
        progress=0,
        caption_style="simple",
        export_height=1280,
        facecam_mode="auto",
    )
    db.add(job)
    db.flush()
    _deduct_credit(db, user, job.id)
    return job


def create_segment_job(db, user: User, channel, segment_path: Path,
                       segment_index: int, live_title: str) -> ClipJob:
    """Queue a clip job for one finished live segment file.

    The segment already exists on disk, so it is an ``upload``-type
    source and ``process_job`` analyses it directly (no download).
    """
    title = (
        f"LIVE: {live_title or channel.live_title or channel.channel_handle} "
        f"— segment {segment_index}"
    )[:200]
    return _create_job(
        db, user,
        title=title,
        source_type="upload",
        stored_path=str(segment_path),
        source_url=vod_url_for(channel.live_video_id or ""),
        original_filename=segment_path.name,
    )


def vod_job_exists(db, user_id: int, video_id: str) -> bool:
    """True when a source for this VOD URL already exists for the user."""
    url = vod_url_for(video_id)
    return (
        db.query(SourceVideo)
        .filter(SourceVideo.user_id == user_id, SourceVideo.source_url == url)
        .first()
        is not None
    )


def vod_is_available(video_id: str) -> bool:
    """Lightweight probe: is the finished VOD fetchable right now?

    A stream that has just ended often has no playable VOD for a few
    minutes; creating the job before that would burn its 3 attempts on
    download failures, so the hook probes first and retries on the next
    live check instead.
    """
    try:
        info = _extract_info_bounded(vod_url_for(video_id), timeout=45)
    except LiveCaptureError:
        return False
    return bool(info) and not info.get("is_live") and bool(info.get("duration"))


def create_vod_job(db, user: User, channel, video_id: str,
                   live_title: str = "") -> ClipJob | None:
    """Queue a clip job for a finished stream's VOD (or None if one
    already exists for this VOD). The job is ``source_type="url"`` with
    an empty ``stored_path``: ``process_job`` downloads it through the
    existing ``download_source_url`` path, exactly like a user-pasted
    URL job.
    """
    if vod_job_exists(db, user.id, video_id):
        return None
    title = f"LIVE VOD: {live_title or channel.live_title or video_id}"[:200]
    return _create_job(
        db, user,
        title=title,
        source_type="url",
        stored_path="",
        source_url=vod_url_for(video_id),
    )


# ---------------------------------------------------------------------------
# Moment scoring (the app's own pipeline scorer, no new "AI")
# ---------------------------------------------------------------------------
def score_segment_file(path: str) -> dict:
    """Rank the clip-worthy moments in a finished segment/source file.

    Runs the real pipeline scorer from ``pipeline/analyze.py`` (scene
    cuts + per-second audio energy; speech is 0 without a transcript,
    exactly as in ``analyze_source``) and returns the ranked candidates
    WITHOUT creating clips or a job. Raises FileNotFoundError /
    RuntimeError when the file is missing or unreadable.
    """
    info = probe_source(path)
    duration = info["duration_s"]
    cuts = detect_scene_cuts(path, duration)
    energy = audio_energy_per_second(path, duration) if info["has_audio"] else {}
    candidates = [
        _score_candidate(s, e, duration, cuts, energy, [], False)
        for (s, e) in _candidate_windows(duration, cuts)
    ]
    candidates.sort(key=lambda c: (-c["score"], c["start_s"], c["end_s"]))
    picked: list[dict] = []
    for cand in candidates:
        overlap = False
        for kept in picked:
            inter = max(0.0, min(cand["end_s"], kept["end_s"])
                        - max(cand["start_s"], kept["start_s"]))
            shorter = min(cand["end_s"] - cand["start_s"],
                          kept["end_s"] - kept["start_s"])
            if shorter > 0 and inter / shorter > 0.5:
                overlap = True
                break
        if not overlap:
            picked.append(cand)
        if len(picked) >= 5:
            break
    return {
        "duration_s": duration,
        "width": info["width"],
        "height": info["height"],
        "has_audio": info["has_audio"],
        "has_transcript": False,
        "note": (
            "Scored with the pipeline's scene-cut + audio-energy scorer; "
            "no transcript was available, so the speech component is 0."
        ),
        "candidates": picked or candidates[:1],
    }


# ---------------------------------------------------------------------------
# Background capture loop (daemon thread in the web process)
# ---------------------------------------------------------------------------
_running: dict[str, threading.Thread] = {}
# RLock (not Lock): start_live_capture holds it while calling
# capture_in_progress, which acquires it again — a plain Lock would
# deadlock the calling thread there.
_running_lock = threading.RLock()


def _marker_path(channel_id: int, video_id: str) -> Path:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in video_id if c.isalnum() or c in ("-", "_"))
    return LIVE_DIR / f"ch{channel_id}_{safe}.running"


def _marker_pid(marker: Path) -> int | None:
    """PID recorded in a marker file, or None when unreadable."""
    try:
        return int(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def capture_in_progress(channel_id: int, video_id: str) -> bool:
    """True when a capture loop for this channel+video is running.

    A marker file whose PID is dead (web process restarted) is stale
    and is removed, so a restart can never block future captures.
    """
    key = f"{channel_id}:{video_id}"
    with _running_lock:
        thread = _running.get(key)
        if thread is not None and thread.is_alive():
            return True
    marker = _marker_path(channel_id, video_id)
    if not marker.is_file():
        return False
    if _pid_alive(_marker_pid(marker)):
        return True
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def stop_live_capture(channel_id: int, video_id: str) -> None:
    """Stop a running capture loop (it checks the marker each segment)."""
    try:
        _marker_path(channel_id, video_id).unlink(missing_ok=True)
    except OSError:
        pass


def _capture_loop(channel_id: int, user_id: int, video_id: str,
                  live_title: str, segment_seconds: int,
                  max_segments: int) -> None:
    """Record consecutive segments until the stream ends / cap / stop.

    Runs in a daemon thread with its own DB session. Each finished
    segment is queued as a ClipJob for the worker; this loop never calls
    ``process_job`` itself. Every outcome is written to the activity
    feed so a failed capture is visible instead of silent.
    """
    key = f"{channel_id}:{video_id}"
    marker = _marker_path(channel_id, video_id)
    marker.write_text(str(os.getpid()), encoding="utf-8")
    video_url = vod_url_for(video_id)
    user_dir = STORAGE_DIR / "uploads" / str(user_id) / "live"
    try:
        for segment_index in range(1, max_segments + 1):
            if not marker.is_file():
                break  # stopped via stop_live_capture()
            try:
                resolved = resolve_live_stream_url(video_url)
            except LiveCaptureError as exc:
                db = SessionLocal()
                try:
                    log_activity(db, user_id, "yt_live_capture_ended",
                                 f"Live capture stopped for '{live_title or video_id}': {exc}")
                    db.commit()
                finally:
                    db.close()
                break
            try:
                segment = record_segment(
                    resolved["stream_url"],
                    user_dir / f"live_seg_{uuid.uuid4().hex}.mp4",
                    segment_seconds,
                )
            except LiveCaptureError as exc:
                db = SessionLocal()
                try:
                    log_activity(db, user_id, "yt_live_capture_failed",
                                 f"Live capture failed for '{live_title or video_id}': {exc}")
                    db.commit()
                finally:
                    db.close()
                break
            db = SessionLocal()
            try:
                user = db.get(User, user_id)
                channel = db.get(YTChannel, channel_id)
                if user is None or channel is None:
                    break
                try:
                    job = create_segment_job(
                        db, user, channel, segment, segment_index,
                        live_title or resolved["title"],
                    )
                    if segment_seconds >= 60:
                        length = f"{segment_seconds // 60} min"
                    else:
                        length = f"{segment_seconds}s"
                    log_activity(
                        db, user_id, "yt_live_segment",
                        f"Live segment {segment_index} captured "
                        f"({length}) — clip job #{job.id} queued.",
                    )
                except LiveCaptureError as exc:
                    log_activity(db, user_id, "yt_live_capture_failed",
                                 f"Live segment {segment_index} captured but no job created: {exc}")
                    db.rollback()
                    # Keep the segment file; the user can clip it manually.
                    db.commit()
                    break
                db.commit()
            finally:
                db.close()
    finally:
        # Only remove the marker when it is still ours (another capture
        # for the same video could have started after we stopped).
        try:
            if _marker_pid(marker) == os.getpid():
                marker.unlink(missing_ok=True)
        except OSError:
            pass
        with _running_lock:
            _running.pop(key, None)


def start_live_capture(channel_id: int, user_id: int, video_id: str,
                       live_title: str = "",
                       segment_seconds: int = SEGMENT_SECONDS,
                       max_segments: int = MAX_SEGMENTS_PER_STREAM) -> bool:
    """Start the background capture loop. False when one is already
    running for this channel+video (no duplicate capture)."""
    key = f"{channel_id}:{video_id}"
    with _running_lock:
        thread = _running.get(key)
        if thread is not None and thread.is_alive():
            return False
        if capture_in_progress(channel_id, video_id):
            return False
        thread = threading.Thread(
            target=_capture_loop,
            args=(channel_id, user_id, video_id, live_title,
                  segment_seconds, max_segments),
            name=f"live-capture-{channel_id}-{video_id}",
            daemon=True,
        )
        _running[key] = thread
        thread.start()
        return True


# ---------------------------------------------------------------------------
# Hook: called from the live-status check path (yt router / API router)
# ---------------------------------------------------------------------------
def on_live_status_change(db, channel, result: dict, *,
                          prev_live_video_id: str = "",
                          was_live: bool | None = None) -> dict:
    """React to a live-status check result. Never raises.

    * live + authorized   -> start the background capture loop.
    * offline + a stream just ended (``prev_live_video_id``) + authorized
      -> if the VOD is fetchable, queue a VOD job; if not ready yet,
      report ``vod_not_ready`` so the next check retries; if a VOD job
      already exists, do nothing.
    * anything else / not authorized -> ``skipped`` (monitoring only).

    ``db`` is the caller's session; rows created here are committed by
    the caller (the check handler). The capture thread opens its own
    session, so it is safe to start from a request handler.
    """
    try:
        status = (result or {}).get("status", "")
        if getattr(channel, "ingestion_state", "") != "authorized":
            return {"action": "skipped", "reason": "channel_not_authorized"}
        user = db.get(User, channel.user_id)
        if user is None:
            return {"action": "skipped", "reason": "user_not_found"}

        if status == "live":
            video_id = (result.get("live_video_id") or "").strip()
            if not video_id:
                return {"action": "skipped", "reason": "no_live_video_id"}
            started = start_live_capture(
                channel.id, user.id, video_id,
                live_title=(result.get("live_title") or ""),
            )
            if started:
                log_activity(db, user.id, "yt_live_capture_started",
                             f"Live capture started: {result.get('live_title') or video_id}")
                return {"action": "capture_started", "video_id": video_id}
            return {"action": "capture_already_running", "video_id": video_id}

        if status == "offline" and prev_live_video_id:
            video_id = prev_live_video_id.strip()
            if vod_job_exists(db, user.id, video_id):
                return {"action": "vod_already_queued", "video_id": video_id}
            if not vod_is_available(video_id):
                log_activity(
                    db, user.id, "yt_live_vod_pending",
                    f"Stream ended ({channel.live_title or video_id}); "
                    "the VOD is not fetchable yet — it will be picked up on the next check.",
                )
                return {"action": "vod_not_ready", "video_id": video_id}
            try:
                job = create_vod_job(
                    db, user, channel, video_id,
                    live_title=channel.live_title or "",
                )
            except LiveCaptureError as exc:
                log_activity(db, user.id, "yt_live_capture_failed",
                             f"Stream ended but no VOD job could be created: {exc}")
                return {"action": "vod_failed", "error": str(exc)}
            if job is None:
                return {"action": "vod_already_queued", "video_id": video_id}
            log_activity(db, user.id, "yt_live_vod_queued",
                         f"Stream ended — VOD clip job #{job.id} queued.")
            return {"action": "vod_job_created", "job_id": job.id, "video_id": video_id}

        return {"action": "none"}
    except Exception as exc:  # the hook must never break the check itself
        try:
            log_activity(db, channel.user_id, "yt_live_capture_failed",
                         f"Live capture hook failed: {_short_error(str(exc))}")
        except Exception:
            pass
        return {"action": "error", "error": _short_error(str(exc))}
