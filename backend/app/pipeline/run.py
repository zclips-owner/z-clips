"""Clip-job orchestrator.

process_job(job_id) opens its own DB session, loads the ClipJob, and
drives it: queued -> analyzing -> rendering -> qc -> done | failed.
(It never sets "needs_model": a missing transcription model degrades
gracefully to caption-less clips, per the product spec, so that status
is reserved for a future owner-gated requirement and is not triggered
here.)

Idempotency: a job that is already "done" returns immediately without
creating duplicate clips. Re-running a failed/queued job first removes
its previous candidate clips (rows and files) so a retry cannot
duplicate them. Attempts are counted; after 3 attempts the job stays
"failed" with "failed after 3 attempts".
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from ..activity import log_activity
from ..database import SessionLocal
from ..models import Clip, ClipJob, SourceVideo
from .analyze import TRANSCRIPT_NOT_INSTALLED_MSG, analyze_source, load_transcript_for_job
from .qc import run_qc
from .render import render_clip

MAX_ATTEMPTS = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _log(db, job: ClipJob, line: str) -> None:
    stamp = _utcnow().strftime("%H:%M:%S")
    job.log = (job.log or "") + f"[{stamp}] {line}\n"
    job.updated_at = _utcnow()
    db.commit()


def _set_status(db, job: ClipJob, status: str, progress: int) -> None:
    job.status = status
    job.progress = progress
    job.updated_at = _utcnow()
    db.commit()


def _fail(db, job: ClipJob, error: str) -> None:
    job.status = "failed"
    job.error = error[:1000]
    job.updated_at = _utcnow()
    db.commit()


def _other_clip_hashes(db, job: ClipJob) -> list[str]:
    """sha256 digests of the user's other clips (for the QC duplicate check)."""
    digests: list[str] = []
    rows = (
        db.query(Clip)
        .filter(Clip.user_id == job.user_id, Clip.job_id != job.id)
        .filter(Clip.file_path != "")
        .all()
    )
    for row in rows:
        try:
            if row.file_path and os.path.isfile(row.file_path):
                h = hashlib.sha256()
                with open(row.file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                digests.append(h.hexdigest())
        except OSError:
            continue
    return digests


def _remove_old_clips(db, job: ClipJob) -> None:
    """Delete a previous (failed) attempt's candidate clips, rows + files,
    so a retry cannot duplicate them."""
    for clip in db.query(Clip).filter(Clip.job_id == job.id).all():
        for path in (clip.file_path, clip.caption_path, clip.thumb_path):
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        db.delete(clip)
    db.commit()


def process_job(job_id: int) -> None:
    """Process one ClipJob end to end. Never raises to the caller: any
    failure is recorded on the job (status='failed', job.error) and the
    worker loop keeps running."""
    db = SessionLocal()
    try:
        job = db.get(ClipJob, job_id)
        if job is None:
            return
        if job.status == "done":
            return  # idempotent: already finished, do not duplicate clips
        if job.status not in ("queued", "analyzing", "rendering", "qc", "failed"):
            return
        if job.attempts >= MAX_ATTEMPTS:
            _fail(db, job, "failed after 3 attempts")
            return
        job.attempts += 1
        job.error = ""
        db.commit()

        try:
            _remove_old_clips(db, job)
            source = db.get(SourceVideo, job.source_id)
            if source is None:
                raise RuntimeError(f"Source video #{job.source_id} no longer exists.")

            # --- analyzing -------------------------------------------------
            # A re-render job from the clip editor carries an explicit trim
            # range (job.trim_start_s/trim_end_s): render exactly that range
            # as a single clip and skip candidate analysis.
            if job.trim_start_s is not None and job.trim_end_s is not None:
                _set_status(db, job, "analyzing", 10)
                _log(db, job, f"Re-render: using editor trim {job.trim_start_s:g}s–{job.trim_end_s:g}s.")
                candidates = [{
                    "start_s": float(job.trim_start_s),
                    "end_s": float(job.trim_end_s),
                    "score": 100.0,
                    "hook_score": 100.0,
                    "reason": "Editor trim",
                }]
                transcript = load_transcript_for_job(job)
                if transcript:
                    _log(db, job, f"Transcription complete: {len(transcript)} words with timestamps.")
                else:
                    _log(db, job, TRANSCRIPT_NOT_INSTALLED_MSG)
                    _log(db, job, "Continuing without captions: clips will have no caption track.")
                if job.facecam_mode == "auto":
                    _log(db, job, "auto facecam: no separate facecam track detected; full-frame layout used.")
                _log(db, job, "Analysis found 1 candidate moment(s).")
                _set_status(db, job, "analyzing", 30)
            else:
                _set_status(db, job, "analyzing", 10)
                _log(db, job, f"Starting analysis of '{source.title or source.original_filename}'.")
                candidates = analyze_source(db, source, job)
                db.commit()
                transcript = load_transcript_for_job(job)
                if transcript:
                    _log(db, job, f"Transcription complete: {len(transcript)} words with timestamps.")
                else:
                    _log(db, job, TRANSCRIPT_NOT_INSTALLED_MSG)
                    _log(db, job, "Continuing without captions: clips will have no caption track.")
                if job.facecam_mode == "auto":
                    _log(db, job, "auto facecam: no separate facecam track detected; full-frame layout used.")
                if not candidates:
                    raise RuntimeError("Analysis found no usable clip moments in this video.")
                _log(db, job, f"Analysis found {len(candidates)} candidate moment(s).")
                _set_status(db, job, "analyzing", 30)

            # --- create candidate Clip rows --------------------------------
            clips: list[Clip] = []
            for i, cand in enumerate(candidates):
                clip = Clip(
                    job_id=job.id,
                    user_id=job.user_id,
                    source_id=job.source_id,
                    title=f"{job.title or 'Clip'} — part {i + 1}" if job.title else f"Clip {i + 1}",
                    start_s=float(cand["start_s"]),
                    end_s=float(cand["end_s"]),
                    duration_s=float(cand["end_s"]) - float(cand["start_s"]),
                    score=float(cand["score"]),
                    hook_score=float(cand["hook_score"]),
                    status="candidate",
                )
                db.add(clip)
                clips.append(clip)
            db.commit()
            for clip in clips:
                db.refresh(clip)

            # --- rendering -------------------------------------------------
            _set_status(db, job, "rendering", 40)
            other_hashes = _other_clip_hashes(db, job)
            ready = 0
            rendered = 0
            duplicate_only = 0
            for idx, clip in enumerate(clips):
                _log(db, job, f"Rendering clip {idx + 1}/{len(clips)} "
                              f"({clip.start_s:.1f}s–{clip.end_s:.1f}s, score {clip.score}).")
                try:
                    result = render_clip(db, clip, job, source)
                except Exception as exc:
                    _log(db, job, f"Render failed for clip {idx + 1}: {exc}")
                    continue
                clip.file_path = result["file_path"]
                clip.caption_path = result["caption_path"]
                clip.thumb_path = result["thumb_path"]
                clip.width = result["width"]
                clip.height = result["height"]
                db.commit()
                _set_status(db, job, "rendering", 40 + int(40 * (idx + 1) / len(clips)))

                # --- QC ----------------------------------------------------
                _set_status(db, job, "qc", 85)
                qc = run_qc(
                    result["file_path"],
                    result["caption_path"],
                    {
                        "duration_s": clip.duration_s,
                        "width": result["width"],
                        "height": result["height"],
                        "has_audio": bool(source.has_audio),
                        "captions_expected": bool(result["caption_path"]),
                        "other_clip_hashes": other_hashes,
                    },
                )
                clip.qc_passed = bool(qc["passed"])
                clip.qc_report = json.dumps(qc)
                db.commit()
                failed_checks = [c["name"] for c in qc["checks"]
                                 if not c["passed"] and c.get("severity", "fail") == "fail"]
                rendered += 1
                if failed_checks == ["duplicate_check"]:
                    duplicate_only += 1
                if qc["passed"]:
                    ready += 1
                    _log(db, job, f"Clip {idx + 1} passed QC and is ready.")
                    log_activity(db, job.user_id, "clip_ready",
                                 f"Clip '{clip.title}' is ready ({clip.duration_s:.0f}s).")
                    db.commit()
                else:
                    _log(db, job, f"Clip {idx + 1} failed QC: {', '.join(failed_checks) or 'unknown check'}.")

            if ready == 0:
                if rendered > 0 and duplicate_only == rendered:
                    detail = ("All rendered clips are duplicates of clips you already have "
                              "(identical file); no new clip was created.")
                else:
                    detail = "No clip rendered and passed QC; the job can be retried."
                if job.attempts >= MAX_ATTEMPTS:
                    _fail(db, job, f"failed after 3 attempts: {detail}")
                else:
                    _fail(db, job, detail)
                return

            _set_status(db, job, "done", 100)
            _log(db, job, f"Done: {ready} of {len(clips)} clip(s) ready.")
        except Exception as exc:
            db.rollback()
            job = db.get(ClipJob, job_id)
            if job is None:
                return
            if job.attempts >= MAX_ATTEMPTS:
                _fail(db, job, f"failed after 3 attempts: {exc}")
            else:
                _fail(db, job, str(exc) or exc.__class__.__name__)
            try:
                _log(db, job, f"Job failed: {exc}")
            except Exception:
                pass
    finally:
        db.close()
