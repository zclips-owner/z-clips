"""Customer routes for Z CLIPS.

Implements exactly the customer routes in docs/ROUTE_CONTEXT_SPEC.md:
dashboard, clips, clips/new, clips/jobs/{id}, clips/{id} (view/edit),
clips/{id}/approve, clips/{id}/reject, downloads, downloads/{id},
subscription, settings, search.

Ownership is checked server-side on every object: a customer may only
access their own rows (403 otherwise). The site owner may *view* any
job/clip (read-only) but may not approve/reject/edit another user's clip.
Every POST validates CSRF and enforces its rate-limit bucket.
"""
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..activity import log_activity
from ..config import MAX_UPLOAD_MB
from ..database import get_db
from ..deps import base_context, check_rate_limit, require_user, validate_csrf
from ..models import (
    Activity,
    AppSetting,
    Clip,
    ClipJob,
    CreditLedger,
    SourceVideo,
    Subscription,
    User,
)
from ..plans import (
    CAPTION_STYLES,
    caption_style_allowed,
    entitlements_for,
    export_heights_allowed,
    promotion_is_active,
)
from ..render import render
from ..services.billing import get_or_create_promotion, refresh_subscription_state
from ..storage import is_allowed_video, safe_filename, user_upload_dir

router = APIRouter()

NO_CREDITS_MSG = "No clip credits left — renew or upgrade your plan."

SUPPORTED_URL_HOSTS = {
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "dailymotion.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "twitch.tv",
}

FACE_CAM_MODES = {"auto", "manual", "off"}

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_unlimited(user: User) -> bool:
    """Owner / complimentary-premium users have unlimited credits."""
    return user.role == "owner" or bool(user.complimentary_premium)


def _caption_style_options(user: User) -> list[dict]:
    ent = entitlements_for(user)
    return [
        {
            "key": key,
            "name": CAPTION_STYLES[key]["name"],
            "description": CAPTION_STYLES[key]["description"],
        }
        for key in ent["caption_style_list"]
    ]


def _clip_new_context(request: Request, user: User, error: str | None = None) -> dict:
    ent = entitlements_for(user)
    can_create = _is_unlimited(user) or user.credits >= 1
    return {
        **base_context(request, user),
        "entitlements": ent,
        "credits": user.credits,
        "caption_styles": _caption_style_options(user),
        "export_heights": export_heights_allowed(user),
        "can_create": can_create,
        "error": error,
    }


def _editor_context(
    request: Request, user: User, clip: Clip, job: ClipJob | None, error: str | None = None
) -> dict:
    ent = entitlements_for(user)
    can_edit = bool(
        ent["manual_editing"] and clip.user_id == user.id and clip.status == "candidate"
    )
    return {
        **base_context(request, user),
        "clip": clip,
        "job": job,
        "entitlements": ent,
        "caption_styles": _caption_style_options(user),
        "export_heights": export_heights_allowed(user),
        "can_edit": can_edit,
        "error": error,
    }


def _get_pref(db: Session, user_id: int, key: str, default: str) -> str:
    row = db.get(AppSetting, f"pref:{user_id}:{key}")
    return row.value if row is not None and row.value else default


def _set_pref(db: Session, user_id: int, key: str, value: str) -> None:
    full_key = f"pref:{user_id}:{key}"
    row = db.get(AppSetting, full_key)
    if row is None:
        row = AppSetting(key=full_key, value=value)
        db.add(row)
    else:
        row.value = value
        row.updated_at = _utcnow()


def _settings_context(
    request: Request,
    user: User,
    db: Session,
    message: str | None = None,
    error: str | None = None,
    default_caption_style: str | None = None,
    default_export_height: int | None = None,
) -> dict:
    ent = entitlements_for(user)
    allowed_styles = ent["caption_style_list"]
    allowed_heights = export_heights_allowed(user)
    if default_caption_style is None:
        stored = _get_pref(db, user.id, "caption_style", "simple")
        default_caption_style = stored if stored in allowed_styles else allowed_styles[0]
    if default_export_height is None:
        try:
            stored_h = int(_get_pref(db, user.id, "export_height", "1280"))
        except ValueError:
            stored_h = 1280
        default_export_height = stored_h if stored_h in allowed_heights else allowed_heights[0]
    return {
        **base_context(request, user),
        "user": user,
        "message": message,
        "error": error,
        "default_caption_style": default_caption_style,
        "default_export_height": default_export_height,
        "caption_styles": _caption_style_options(user),
        "export_heights": allowed_heights,
    }


def _latest_active_subscription(db: Session, user_id: int) -> Subscription | None:
    return db.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == "active")
        .order_by(Subscription.id.desc())
    )


def _deduct_credit(db: Session, user: User, job_id: int) -> None:
    """Decrement 1 credit + increment clips_used + ledger entry.

    Unlimited (owner / complimentary) users are not charged. Callers must
    commit once after creating the job/activity rows.
    """
    if _is_unlimited(user):
        return
    user.credits -= 1
    user.clips_used += 1
    db.add(
        CreditLedger(
            user_id=user.id,
            delta=-1,
            reason="Clip job created",
            ref_type="clip_job",
            ref_id=job_id,
        )
    )



# ---------------------------------------------------------------- dashboard
@router.get("/dashboard")
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if refresh_subscription_state(db, user):
        db.commit()
        db.refresh(user)
    ent = entitlements_for(user)
    promotion = get_or_create_promotion(db)
    promo_active = promotion_is_active(promotion)
    subscription = _latest_active_subscription(db, user.id)
    recent_activity = list(
        db.scalars(
            select(Activity)
            .where(Activity.user_id == user.id)
            .order_by(Activity.created_at.desc(), Activity.id.desc())
            .limit(10)
        )
    )
    total = db.scalar(select(func.count(Clip.id)).where(Clip.user_id == user.id)) or 0
    approved = (
        db.scalar(
            select(func.count(Clip.id)).where(Clip.user_id == user.id, Clip.status == "approved")
        )
        or 0
    )
    candidates = (
        db.scalar(
            select(func.count(Clip.id)).where(Clip.user_id == user.id, Clip.status == "candidate")
        )
        or 0
    )
    yt_status = "available" if ent["youtube_live_monitoring"] else "locked"
    return render(
        request,
        "dashboard.html",
        {
            **base_context(request, user),
            "user": user,
            "entitlements": ent,
            "credits": user.credits,
            "remaining": user.credits,
            "plan_name": ent["name"],
            "subscription": subscription,
            "promo_active": promo_active,
            "promotion": promotion,
            "recent_activity": recent_activity,
            "yt_status": yt_status,
            "clip_counts": {"total": total, "approved": approved, "candidates": candidates},
        },
    )


# ---------------------------------------------------------------- clips list
@router.get("/clips")
def my_clips(
    request: Request,
    q: str = Query(default=""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    q = (q or "").strip()
    stmt = select(Clip).where(Clip.user_id == user.id)
    if q:
        stmt = stmt.where(Clip.title.ilike(f"%{q}%"))
    clips = list(db.scalars(stmt.order_by(Clip.created_at.desc(), Clip.id.desc())))
    jobs = list(
        db.scalars(
            select(ClipJob)
            .where(ClipJob.user_id == user.id)
            .order_by(ClipJob.created_at.desc(), ClipJob.id.desc())
        )
    )
    return render(
        request,
        "clips.html",
        {**base_context(request, user), "clips": clips, "jobs": jobs, "q": q},
    )


# ---------------------------------------------------------------- create clip
@router.get("/clips/new")
def clip_new_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return render(request, "clip_new.html", _clip_new_context(request, user))


@router.post("/clips/new")
async def clip_new_submit(
    request: Request,
    title: str = Form(default=""),
    caption_style: str = Form(default="simple"),
    export_height: str = Form(default="1280"),
    facecam_mode: str = Form(default="auto"),
    source_url: str = Form(default=""),
    csrf_token: str = Form(default=""),
    video: UploadFile | None = File(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "clip_create", key=str(user.id))

    def fail(error: str):
        return render(request, "clip_new.html", _clip_new_context(request, user, error), 400)

    title = (title or "").strip()
    source_url = (source_url or "").strip()
    caption_style = (caption_style or "").strip()
    facecam_mode = (facecam_mode or "auto").strip()
    has_file = video is not None and bool(video.filename)

    if not title:
        return fail("Please give your clip a title.")
    if len(title) > 200:
        return fail("Title must be 200 characters or fewer.")
    if not caption_style_allowed(user, caption_style):
        return fail("That caption style is not available on your plan — please upgrade to use it.")
    try:
        export_height_int = int(export_height)
    except (TypeError, ValueError):
        return fail("Please choose a valid export height.")
    if export_height_int not in export_heights_allowed(user):
        return fail("That export height is not available on your plan — please upgrade to use it.")
    if facecam_mode not in FACE_CAM_MODES:
        return fail("Please choose a valid facecam mode.")
    if facecam_mode == "manual" and not entitlements_for(user)["advanced_positioning"]:
        return fail("Manual facecam positioning requires the Pro plan.")
    if not has_file and not source_url:
        return fail("Upload a video file or paste a source URL.")
    if has_file and not is_allowed_video(video.filename or ""):
        return fail("That video format is not supported. Please upload an MP4, MOV, MKV, WebM, AVI or M4V file.")
    if source_url and not has_file:
        parsed = urlparse(source_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            return fail("Source URL must start with http:// or https://.")
        if not any(host == h or host.endswith("." + h) for h in SUPPORTED_URL_HOSTS):
            return fail(
                "That source is not supported. Supported sources: YouTube, Vimeo, "
                "Dailymotion, Facebook, Instagram, TikTok and Twitch."
            )
    if not _is_unlimited(user) and user.credits < 1:
        return fail(NO_CREDITS_MSG)

    # Stream an uploaded file to disk in chunks (never whole file in memory).
    stored_path = ""
    original_filename = ""
    file_size = 0
    if has_file:
        original_filename = safe_filename(video.filename or "video")
        dest = user_upload_dir(user.id) / f"{uuid.uuid4().hex}_{original_filename}"
        max_bytes = MAX_UPLOAD_MB * 1024 * 1024
        try:
            with dest.open("wb") as out:
                while True:
                    chunk = await video.read(1024 * 1024)
                    if not chunk:
                        break
                    file_size += len(chunk)
                    if file_size > max_bytes:
                        out.close()
                        dest.unlink(missing_ok=True)
                        return fail(f"Video file is too large — the limit is {MAX_UPLOAD_MB} MB.")
                    out.write(chunk)
        except Exception:
            if dest.exists():
                dest.unlink(missing_ok=True)
            return fail("Could not save the uploaded video. Please try again.")
        stored_path = str(dest)

    source = SourceVideo(
        user_id=user.id,
        title=title,
        source_type="upload" if has_file else "url",
        original_filename=original_filename,
        stored_path=stored_path,
        source_url="" if has_file else source_url,
        file_size=file_size,
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
        caption_style=caption_style,
        export_height=export_height_int,
        facecam_mode=facecam_mode,
    )
    db.add(job)
    db.flush()
    _deduct_credit(db, user, job.id)
    log_activity(db, user.id, "clip_created", f"Clip job created: {title}")
    db.commit()
    return RedirectResponse(f"/clips/jobs/{job.id}", status_code=303)


# ---------------------------------------------------------------- job status
@router.get("/clips/jobs/{job_id}")
def job_page(
    request: Request,
    job_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    job = db.get(ClipJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.user_id != user.id and not user.is_owner:
        raise HTTPException(status_code=403, detail="You can only view your own jobs.")
    clips = list(
        db.scalars(
            select(Clip).where(Clip.job_id == job.id).order_by(Clip.created_at.desc(), Clip.id.desc())
        )
    )
    source = db.get(SourceVideo, job.source_id)
    return render(
        request,
        "job.html",
        {
            **base_context(request, user),
            "job": job,
            "clips": clips,
            "source": source,
            "log_lines": job.log.splitlines() if job.log else [],
        },
    )


# ---------------------------------------------------------------- clip editor
@router.get("/clips/{clip_id}")
def clip_editor_page(
    request: Request,
    clip_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    if clip.user_id != user.id and not user.is_owner:
        raise HTTPException(status_code=403, detail="You can only view your own clips.")
    job = db.get(ClipJob, clip.job_id)
    return render(request, "clip_editor.html", _editor_context(request, user, clip, job))


@router.post("/clips/{clip_id}/edit")
def clip_edit_submit(
    request: Request,
    clip_id: int,
    start_s: str = Form(default=""),
    end_s: str = Form(default=""),
    caption_style: str = Form(default="simple"),
    export_height: str = Form(default="1280"),
    facecam_mode: str = Form(default="auto"),
    facecam_x: str | None = Form(default=None),
    facecam_y: str | None = Form(default=None),
    facecam_w: str | None = Form(default=None),
    facecam_h: str | None = Form(default=None),
    csrf_token: str = Form(default=""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    if clip.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own clips.")
    job = db.get(ClipJob, clip.job_id)
    source = db.get(SourceVideo, clip.source_id)

    def fail(error: str):
        return render(request, "clip_editor.html", _editor_context(request, user, clip, job, error), 400)

    ent = entitlements_for(user)
    if not ent["manual_editing"]:
        return fail("Manual editing requires the Lite or Pro plan.")
    if clip.status != "candidate":
        return fail("Only candidate clips can be edited.")
    if not caption_style_allowed(user, (caption_style or "").strip()):
        return fail("That caption style is not available on your plan — please upgrade to use it.")
    try:
        export_height_int = int(export_height)
    except (TypeError, ValueError):
        return fail("Please choose a valid export height.")
    if export_height_int not in export_heights_allowed(user):
        return fail("That export height is not available on your plan — please upgrade to use it.")
    facecam_mode = (facecam_mode or "auto").strip()
    if facecam_mode not in FACE_CAM_MODES:
        return fail("Please choose a valid facecam mode.")
    if facecam_mode == "manual" and not ent["advanced_positioning"]:
        return fail("Manual facecam positioning requires the Pro plan.")
    try:
        start = float(start_s)
        end = float(end_s)
    except (TypeError, ValueError):
        return fail("Start and end must be numbers (seconds).")
    if not (0 <= start < end):
        return fail("Start must be 0 or later and earlier than the end time.")
    if source is not None and source.duration_s is not None and end > source.duration_s:
        return fail("End time is after the end of the source video.")
    coords: dict[str, float | None] = {}
    for name, raw in (
        ("facecam_x", facecam_x),
        ("facecam_y", facecam_y),
        ("facecam_w", facecam_w),
        ("facecam_h", facecam_h),
    ):
        if raw is None or str(raw).strip() == "":
            coords[name] = None
        else:
            try:
                coords[name] = float(raw)
            except (TypeError, ValueError):
                return fail("Facecam position values must be numbers.")
    if not _is_unlimited(user) and user.credits < 1:
        return fail(NO_CREDITS_MSG)

    # Trim range is stored on the job so the worker honours it (and the log
    # records it for the job page).
    new_job = ClipJob(
        user_id=user.id,
        source_id=clip.source_id,
        title=clip.title,
        status="queued",
        progress=0,
        log=f"Re-render of clip #{clip.id}: trim {start:g}s–{end:g}s.",
        caption_style=(caption_style or "").strip(),
        export_height=export_height_int,
        trim_start_s=start,
        trim_end_s=end,
        facecam_mode=facecam_mode,
        facecam_x=coords["facecam_x"],
        facecam_y=coords["facecam_y"],
        facecam_w=coords["facecam_w"],
        facecam_h=coords["facecam_h"],
    )
    db.add(new_job)
    db.flush()
    _deduct_credit(db, user, new_job.id)
    log_activity(db, user.id, "clip_created", f"Clip re-render created: {clip.title}")
    db.commit()
    return RedirectResponse(f"/clips/jobs/{new_job.id}", status_code=303)


# ---------------------------------------------------------------- approve / reject
@router.post("/clips/{clip_id}/approve")
def clip_approve(
    request: Request,
    clip_id: int,
    csrf_token: str = Form(default=""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    if clip.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only approve your own clips.")
    clip.status = "approved"
    clip.approved_at = _utcnow()
    log_activity(db, user.id, "clip_approved", f"Clip approved: {clip.title}")
    db.commit()
    return RedirectResponse("/downloads", status_code=303)


@router.post("/clips/{clip_id}/reject")
def clip_reject(
    request: Request,
    clip_id: int,
    csrf_token: str = Form(default=""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    if clip.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only reject your own clips.")
    clip.status = "rejected"
    log_activity(db, user.id, "clip_rejected", f"Clip rejected: {clip.title}")
    db.commit()
    return RedirectResponse("/clips", status_code=303)


# ---------------------------------------------------------------- downloads
@router.get("/downloads")
def downloads_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    clips = list(
        db.scalars(
            select(Clip)
            .where(Clip.user_id == user.id, Clip.status == "approved")
            .order_by(Clip.created_at.desc(), Clip.id.desc())
        )
    )
    return render(request, "downloads.html", {**base_context(request, user), "clips": clips})


@router.get("/downloads/{clip_id}")
def download_clip(
    request: Request,
    clip_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    if clip.user_id != user.id and not user.is_owner:
        raise HTTPException(status_code=403, detail="You can only download your own clips.")
    if clip.status != "approved":
        raise HTTPException(status_code=403, detail="Only approved clips can be downloaded.")
    if not clip.file_path or not Path(clip.file_path).is_file():
        raise HTTPException(status_code=404, detail="Clip file is missing.")
    clip.downloads += 1
    log_activity(db, user.id, "clip_downloaded", f"Clip downloaded: {clip.title}")
    db.commit()
    media_type = "video/mp4" if Path(clip.file_path).suffix.lower() == ".mp4" else None
    return FileResponse(clip.file_path, media_type=media_type, filename=Path(clip.file_path).name)


# ---------------------------------------------------------------- subscription
@router.get("/subscription")
def subscription_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if refresh_subscription_state(db, user):
        db.commit()
        db.refresh(user)
    ent = entitlements_for(user)
    promotion = get_or_create_promotion(db)
    promo_active = promotion_is_active(promotion)
    subscription = _latest_active_subscription(db, user.id)
    history = list(
        db.scalars(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.id.desc())
        )
    )
    ledger = list(
        db.scalars(
            select(CreditLedger)
            .where(CreditLedger.user_id == user.id)
            .order_by(CreditLedger.created_at.desc(), CreditLedger.id.desc())
            .limit(20)
        )
    )
    return render(
        request,
        "subscription.html",
        {
            **base_context(request, user),
            "user": user,
            "entitlements": ent,
            "subscription": subscription,
            "history": history,
            "ledger": ledger,
            "promo_active": promo_active,
            "promotion": promotion,
            "plan_change_note": "Plan changes need owner confirmation — contact support.",
        },
    )


# ---------------------------------------------------------------- settings
@router.get("/settings")
def settings_page(
    request: Request,
    message: str | None = Query(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return render(request, "settings.html", _settings_context(request, user, db, message=message))


@router.post("/settings")
def settings_submit(
    request: Request,
    display_name: str = Form(default=""),
    email: str = Form(default=""),
    default_caption_style: str = Form(default="simple"),
    default_export_height: str = Form(default="1280"),
    csrf_token: str = Form(default=""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))

    display_name = (display_name or "").strip()
    email = (email or "").strip().lower()
    default_caption_style = (default_caption_style or "").strip()
    try:
        export_height_int = int(default_export_height)
    except (TypeError, ValueError):
        export_height_int = -1

    def fail(error: str):
        return render(
            request,
            "settings.html",
            _settings_context(
                request,
                user,
                db,
                error=error,
                default_caption_style=default_caption_style,
                default_export_height=export_height_int if export_height_int > 0 else 1280,
            ),
            400,
        )

    if len(display_name) > 80:
        return fail("Display name must be 80 characters or fewer.")
    if not EMAIL_RE.fullmatch(email):
        return fail("Please enter a valid email address.")
    existing = db.scalar(
        select(User).where(func.lower(User.email) == email, User.id != user.id)
    )
    if existing is not None:
        return fail("That email is already used by another account.")
    if not caption_style_allowed(user, default_caption_style):
        return fail("That caption style is not available on your plan.")
    if export_height_int not in export_heights_allowed(user):
        return fail("That export height is not available on your plan.")

    user.display_name = display_name
    user.email = email
    _set_pref(db, user.id, "caption_style", default_caption_style)
    _set_pref(db, user.id, "export_height", str(export_height_int))
    db.commit()
    return RedirectResponse("/settings?message=Settings+saved.", status_code=303)


# ---------------------------------------------------------------- search
@router.get("/search")
def search_page(
    request: Request,
    q: str = Query(default=""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    q = (q or "").strip()
    clips: list[Clip] = []
    users: list[User] = []
    if q:
        clips = list(
            db.scalars(
                select(Clip)
                .where(Clip.user_id == user.id, Clip.title.ilike(f"%{q}%"))
                .order_by(Clip.created_at.desc(), Clip.id.desc())
                .limit(50)
            )
        )
        if user.is_owner:
            users = list(
                db.scalars(
                    select(User)
                    .where(User.username.ilike(f"%{q}%"))
                    .order_by(User.username)
                    .limit(50)
                )
            )
    return render(
        request,
        "search.html",
        {**base_context(request, user), "q": q, "clips": clips, "users": users},
    )
