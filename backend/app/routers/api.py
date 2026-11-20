"""JSON API router for Z CLIPS (``/api/v1/*``).

Implements API_CONTRACT.md exactly. This router reuses the business logic
of the HTML routers (validation, credit deduction, billing, promotion
seats, YouTube checks) and only changes the transport: JSON in/out,
session-cookie auth with 401 instead of 303 redirects, and the CSRF token
carried in the ``X-CSRF-Token`` header instead of a form field.

Nothing here exposes ``password_hash``, ``SESSION_SECRET``,
``OWNER_SETUP_TOKEN`` or any API key.
"""
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..activity import ACTIVITY_LABELS, log_activity
from ..config import (
    DATABASE_URL,
    MAX_SCREENSHOT_MB,
    MAX_UPLOAD_MB,
    NAYAPAY_ACCOUNT_HOLDER,
    NAYAPAY_NUMBER,
    NAYAPAY_QR_INSTRUCTION,
    NAYAPAY_QR_PATH,
    YOUTUBE_API_KEY,
)
from ..database import get_db
from ..deps import check_rate_limit, csrf_token_for, current_user, validate_csrf
from ..models import (
    Activity,
    AppSetting,
    AuditLog,
    Clip,
    ClipJob,
    CreditLedger,
    ModelInfo,
    PaymentRequest,
    SourceVideo,
    Subscription,
    SupportMessage,
    SupportThread,
    User,
    YTChannel,
)
from ..plans import (
    CAPTION_STYLES,
    PLANS,
    caption_style_allowed,
    entitlements_for,
    export_heights_allowed,
    promotion_is_active,
)
from ..security import hash_password, verify_password
from ..services import billing
from ..services.billing import (
    apply_payment,
    get_or_create_promotion,
    refresh_subscription_state,
    reject_payment,
    reserve_promo_seat,
)
from ..services.youtube import check_live_status, parse_channel_input
from ..storage import (
    is_allowed_image,
    is_allowed_video,
    safe_filename,
    storage_stats,
    user_payment_dir,
    user_upload_dir,
)
from .customer import (
    FACE_CAM_MODES,
    NO_CREDITS_MSG,
    SUPPORTED_URL_HOSTS,
    _caption_style_options,
    _deduct_credit,
    _get_pref,
    _is_unlimited,
    _latest_active_subscription,
    _set_pref,
)
from .payments import _pricing

try:
    from ..services.models_manager import install_model, list_models
except ImportError:  # pragma: no cover - model manager not installed yet
    install_model = None
    list_models = None

router = APIRouter(prefix="/api/v1", tags=["api"])


class _ApiRoute(APIRoute):
    """Render every HTTPException raised by an API endpoint (including the
    auth dependencies) as the contract's error shape
    ``{"error": "<message>"}``. FastAPI's default handler would render
    ``{"detail": ...}`` instead; this keeps the conversion inside api.py
    so no HTML route's error rendering is touched."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except HTTPException as exc:
                return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

        return wrapped


router.route_class = _ApiRoute

_PROCESSING_STATUSES = ("analyzing", "rendering", "qc")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SECRET_KEY_RE = re.compile(r"secret|token|password|api[_-]?key|session", re.IGNORECASE)


# --------------------------------------------------------------------------
# Auth dependencies (JSON variants of deps.py: 401, never a 303 redirect)
# --------------------------------------------------------------------------
def api_require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


def api_require_owner(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in.")
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required.")
    return user


def api_require_pro(request: Request, db: Session = Depends(get_db)) -> User:
    user = api_require_user(request, db)
    if not entitlements_for(user)["youtube_live_monitoring"]:
        raise HTTPException(
            status_code=403,
            detail="YouTube Live monitoring requires the Pro plan.",
        )
    return user


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _err(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_csrf(request: Request) -> None:
    """CSRF for JSON/multipart API calls: token comes from the
    ``X-CSRF-Token`` header (never a form field here)."""
    validate_csrf(request, request.headers.get("x-csrf-token"))


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    return data


def _as_bool(value, default=None):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ("true", "1", "yes", "on"):
            return True
        if value.lower() in ("false", "0", "no", "off"):
            return False
    return default


def _as_int(value, default=None):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=None):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _username_of(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = db.get(User, user_id)
    return row.username if row is not None else None


# --------------------------------------------------------------------------
# Serializers (exact contract shapes)
# --------------------------------------------------------------------------
def user_summary(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "plan": u.plan,
        "effective_plan": u.effective_plan,
        "credits": u.credits,
        "clips_used": u.clips_used,
        "plan_started_at": _iso(u.plan_started_at),
        "plan_ends_at": _iso(u.plan_ends_at),
        "is_active": u.is_active,
        "is_suspended": u.is_suspended,
        "complimentary_premium": u.complimentary_premium,
        "created_at": _iso(u.created_at),
    }


def user_public(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "plan": u.plan,
        "credits": u.credits,
        "clips_used": u.clips_used,
        "plan_ends_at": _iso(u.plan_ends_at),
        "is_active": u.is_active,
        "is_suspended": u.is_suspended,
        "complimentary_premium": u.complimentary_premium,
        "created_at": _iso(u.created_at),
    }


def subscription_ser(s: Subscription | None) -> dict | None:
    if s is None:
        return None
    return {
        "id": s.id,
        "plan": s.plan,
        "started_at": _iso(s.started_at),
        "ends_at": _iso(s.ends_at),
        "credits_granted": s.credits_granted,
        "status": s.status,
        "is_promotional": s.is_promotional,
        "promo_price_pkr": s.promo_price_pkr,
    }


def ledger_ser(row: CreditLedger) -> dict:
    return {
        "id": row.id,
        "delta": row.delta,
        "reason": row.reason,
        "ref_type": row.ref_type,
        "ref_id": row.ref_id,
        "created_at": _iso(row.created_at),
    }


def promotion_ser(p) -> dict:
    return {
        "name": p.name,
        "discount_pct": p.discount_pct,
        "promo_price_pkr": p.promo_price_pkr,
        "normal_price_pkr": p.normal_price_pkr,
        "max_seats": p.max_seats,
        "seats_reserved": p.seats_reserved,
        "seats_used": p.seats_used,
        "seats_remaining": p.seats_remaining,
        "ends_at": _iso(p.ends_at),
        "is_active": p.is_active,
    }


def clip_ser(c: Clip) -> dict:
    return {
        "id": c.id,
        "job_id": c.job_id,
        "source_id": c.source_id,
        "title": c.title,
        "start_s": c.start_s,
        "end_s": c.end_s,
        "duration_s": c.duration_s,
        "score": c.score,
        "hook_score": c.hook_score,
        "width": c.width,
        "height": c.height,
        "status": c.status,
        "qc_passed": c.qc_passed,
        "downloads": c.downloads,
        "created_at": _iso(c.created_at),
        "approved_at": _iso(c.approved_at),
        "thumb_url": f"/api/v1/clips/{c.id}/thumb" if c.thumb_path else None,
        "preview_url": f"/api/v1/clips/{c.id}/preview" if c.file_path else None,
    }


def job_ser(j: ClipJob) -> dict:
    return {
        "id": j.id,
        "title": j.title,
        "status": j.status,
        "progress": j.progress,
        "log": j.log,
        "error": j.error,
        "caption_style": j.caption_style,
        "export_height": j.export_height,
        "trim_start_s": j.trim_start_s,
        "trim_end_s": j.trim_end_s,
        "facecam_mode": j.facecam_mode,
        "attempts": j.attempts,
        "created_at": _iso(j.created_at),
        "updated_at": _iso(j.updated_at),
    }


def source_ser(s: SourceVideo | None) -> dict | None:
    if s is None:
        return None
    return {
        "id": s.id,
        "title": s.title,
        "source_type": s.source_type,
        "original_filename": s.original_filename,
        "duration_s": s.duration_s,
        "width": s.width,
        "height": s.height,
        "has_audio": s.has_audio,
        "file_size": s.file_size,
        "status": s.status,
        "error": s.error,
        "created_at": _iso(s.created_at),
    }


def payment_ser(db: Session, p: PaymentRequest) -> dict:
    return {
        "id": p.id,
        "plan": p.plan,
        "amount_pkr": p.amount_pkr,
        "is_promotional": p.is_promotional,
        "txn_id": p.txn_id,
        "status": p.status,
        "review_note": p.review_note,
        "created_at": _iso(p.created_at),
        "decided_at": _iso(p.decided_at),
        "username": _username_of(db, p.user_id),
    }


def thread_ser(db: Session, t: SupportThread) -> dict:
    return {
        "id": t.id,
        "subject": t.subject,
        "status": t.status,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
        "username": _username_of(db, t.user_id),
    }


def message_ser(m: SupportMessage) -> dict:
    return {
        "id": m.id,
        "thread_id": m.thread_id,
        "author_id": m.author_id,
        "author_role": m.author_role,
        "body": m.body,
        "created_at": _iso(m.created_at),
    }


def activity_ser(a: Activity) -> dict:
    return {
        "id": a.id,
        "kind": a.kind,
        "label": ACTIVITY_LABELS.get(a.kind, a.kind),
        "detail": a.detail,
        "created_at": _iso(a.created_at),
    }


def yt_channel_ser(c: YTChannel) -> dict:
    return {
        "id": c.id,
        "channel_url": c.channel_url,
        "channel_handle": c.channel_handle,
        "channel_id": c.channel_id,
        "display_name": c.display_name,
        "is_live": c.is_live,
        "last_status": c.last_status,
        "last_checked_at": _iso(c.last_checked_at),
        "live_video_id": c.live_video_id,
        "live_title": c.live_title,
        "ingestion_state": c.ingestion_state,
        "authorized_source_note": c.authorized_source_note,
        "created_at": _iso(c.created_at),
    }


def model_ser(m: dict) -> dict:
    return {
        "name": m.get("name", ""),
        "version": m.get("version", "") or "",
        "size_mb": m.get("size_mb", 0),
        "license": m.get("license", ""),
        "status": m.get("status", "not_installed"),
        "error": m.get("error", "") or "",
        "installed_at": m.get("installed_at"),
    }


def _promotion_block(db: Session) -> tuple[dict, bool]:
    promotion = get_or_create_promotion(db)
    db.commit()
    return promotion_ser(promotion), promotion_is_active(promotion)


def _get_clip(db: Session, clip_id: int, user: User) -> Clip:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    if clip.user_id != user.id and not user.is_owner:
        raise HTTPException(status_code=403, detail="You can only view your own clips.")
    return clip


def _get_thread(db: Session, thread_id: int, user: User) -> SupportThread:
    thread = db.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Support thread not found.")
    if user.role != "owner" and thread.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only view your own support threads.")
    return thread


def _thread_messages(db: Session, thread_id: int):
    return db.scalars(
        select(SupportMessage)
        .where(SupportMessage.thread_id == thread_id)
        .order_by(SupportMessage.id.asc())
    ).all()


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------
@router.get("/health")
def api_health():
    return {"ok": True, "service": "z-clips"}


@router.get("/csrf")
def api_csrf(request: Request):
    # csrf_token_for creates a session id when none exists.
    return {"csrf_token": csrf_token_for(request)}


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@router.post("/auth/login")
async def api_login(request: Request, db: Session = Depends(get_db)):
    # Body is parsed here (not via Depends) so a missing CSRF header still
    # fails closed with 403 and malformed JSON with 400.
    return await _login_impl(request, db)


async def _login_impl(request: Request, db: Session):
    check_rate_limit(request, "login")
    _require_csrf(request)
    data = await _json_body(request)
    username = str(data.get("username") or "").strip()
    password = data.get("password") or ""
    user = db.scalar(
        select(User).where(func.lower(User.username) == username.lower())
    )
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return _err("Username ya password sahi nahi hai.", 401)
    request.session.clear()
    request.session["user_id"] = user.id
    return {"user": user_summary(user)}


@router.post("/auth/signup", status_code=201)
async def api_signup(request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "signup")
    _require_csrf(request)
    data = await _json_body(request)
    username = str(data.get("username") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not re.fullmatch(r"[A-Za-z0-9_]{3,40}", username):
        return _err("Username 3–40 characters ka ho; letters, numbers aur underscore use karein.")
    if not email:
        return _err("Please enter an email address.")
    if len(password) < 12:
        return _err("Password kam az kam 12 characters ka hona chahiye.")
    existing = db.scalar(
        select(User).where(
            (func.lower(User.username) == username.lower())
            | (func.lower(User.email) == email)
        )
    )
    if existing:
        return _err("Ye username ya email pehle se registered hai.")
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role="customer",
        plan="free",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session.clear()
    request.session["user_id"] = user.id
    return JSONResponse({"user": user_summary(user)}, status_code=201)


@router.post("/auth/logout")
def api_logout(request: Request):
    _require_csrf(request)
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def api_me(user: User = Depends(api_require_user)):
    return {"user": user_summary(user), "entitlements": entitlements_for(user)}


# --------------------------------------------------------------------------
# Dashboard / plans / subscription / search
# --------------------------------------------------------------------------
@router.get("/dashboard")
def api_dashboard(user: User = Depends(api_require_user), db: Session = Depends(get_db)):
    if refresh_subscription_state(db, user):
        db.commit()
        db.refresh(user)
    ent = entitlements_for(user)
    promo, promo_active = _promotion_block(db)
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
    return {
        "user": user_summary(user),
        "entitlements": ent,
        "credits": user.credits,
        "plan_name": ent["name"],
        "subscription": subscription_ser(subscription),
        "promotion": promo,
        "promo_active": promo_active,
        "recent_activity": [activity_ser(a) for a in recent_activity],
        "clip_counts": {"total": total, "approved": approved, "candidates": candidates},
        "yt_status": "available" if ent["youtube_live_monitoring"] else "locked",
    }


@router.get("/plans")
def api_plans(db: Session = Depends(get_db)):
    promo, promo_active = _promotion_block(db)
    return {
        "plans": {key: dict(value) for key, value in PLANS.items()},
        "promotion": promo,
        "promo_active": promo_active,
    }


@router.get("/subscription")
def api_subscription(user: User = Depends(api_require_user), db: Session = Depends(get_db)):
    if refresh_subscription_state(db, user):
        db.commit()
        db.refresh(user)
    ent = entitlements_for(user)
    promo, promo_active = _promotion_block(db)
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
    return {
        "user": user_summary(user),
        "entitlements": ent,
        "subscription": subscription_ser(subscription),
        "history": [subscription_ser(s) for s in history],
        "ledger": [ledger_ser(row) for row in ledger],
        "promotion": promo,
        "promo_active": promo_active,
        "plan_change_note": "Plan changes need owner confirmation — contact support.",
    }


@router.get("/search")
def api_search(
    q: str = Query(default=""),
    user: User = Depends(api_require_user),
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
    return {
        "q": q,
        "clips": [clip_ser(c) for c in clips],
        "users": [user_public(u) for u in users],
    }


# --------------------------------------------------------------------------
# Clips
# --------------------------------------------------------------------------
@router.get("/clips")
def api_clips(
    q: str = Query(default=""),
    status: str = Query(default=""),
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    q = (q or "").strip()
    stmt = select(Clip).where(Clip.user_id == user.id)
    if q:
        stmt = stmt.where(Clip.title.ilike(f"%{q}%"))
    if status in ("candidate", "approved", "rejected"):
        stmt = stmt.where(Clip.status == status)
    clips = list(db.scalars(stmt.order_by(Clip.created_at.desc(), Clip.id.desc())))
    jobs = list(
        db.scalars(
            select(ClipJob)
            .where(ClipJob.user_id == user.id)
            .order_by(ClipJob.created_at.desc(), ClipJob.id.desc())
        )
    )
    return {"clips": [clip_ser(c) for c in clips], "jobs": [job_ser(j) for j in jobs]}


@router.post("/clips", status_code=201)
async def api_clip_create(
    request: Request,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    """Multipart clip creation — same validation + credit deduction as
    POST /clips/new in the HTML router."""
    _require_csrf(request)
    check_rate_limit(request, "clip_create", key=str(user.id))
    form = await request.form()
    title = (form.get("title") or "").strip() if isinstance(form.get("title"), str) else ""
    caption_style = (form.get("caption_style") or "simple").strip() if isinstance(form.get("caption_style"), str) else "simple"
    export_height = form.get("export_height") or "1280"
    facecam_mode = (form.get("facecam_mode") or "auto").strip() if isinstance(form.get("facecam_mode"), str) else "auto"
    source_url = (form.get("source_url") or "").strip() if isinstance(form.get("source_url"), str) else ""
    video = form.get("video")
    has_file = video is not None and getattr(video, "filename", "")

    if not title:
        return _err("Please give your clip a title.")
    if len(title) > 200:
        return _err("Title must be 200 characters or fewer.")
    if not caption_style_allowed(user, caption_style):
        return _err("That caption style is not available on your plan — please upgrade to use it.")
    try:
        export_height_int = int(export_height)
    except (TypeError, ValueError):
        return _err("Please choose a valid export height.")
    if export_height_int not in export_heights_allowed(user):
        return _err("That export height is not available on your plan — please upgrade to use it.")
    if facecam_mode not in FACE_CAM_MODES:
        return _err("Please choose a valid facecam mode.")
    if facecam_mode == "manual" and not entitlements_for(user)["advanced_positioning"]:
        return _err("Manual facecam positioning requires the Pro plan.")
    if not has_file and not source_url:
        return _err("Upload a video file or paste a source URL.")
    if has_file and not is_allowed_video(video.filename or ""):
        return _err("That video format is not supported. Please upload an MP4, MOV, MKV, WebM, AVI or M4V file.")
    if source_url and not has_file:
        parsed = urlparse(source_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            return _err("Source URL must start with http:// or https://.")
        if not any(host == h or host.endswith("." + h) for h in SUPPORTED_URL_HOSTS):
            return _err(
                "That source is not supported. Supported sources: YouTube, Vimeo, "
                "Dailymotion, Facebook, Instagram, TikTok and Twitch."
            )
    if not _is_unlimited(user) and user.credits < 1:
        return _err(NO_CREDITS_MSG)

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
                        return _err(f"Video file is too large — the limit is {MAX_UPLOAD_MB} MB.")
                    out.write(chunk)
        except Exception:
            if dest.exists():
                dest.unlink(missing_ok=True)
            return _err("Could not save the uploaded video. Please try again.")
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
    return JSONResponse({"job": job_ser(job)}, status_code=201)


@router.get("/clips/jobs/{job_id}")
def api_job(
    job_id: int,
    user: User = Depends(api_require_user),
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
    return {"job": job_ser(job), "clips": [clip_ser(c) for c in clips], "source": source_ser(source)}


@router.get("/clips/{clip_id}")
def api_clip_detail(
    clip_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    clip = _get_clip(db, clip_id, user)
    job = db.get(ClipJob, clip.job_id)
    ent = entitlements_for(user)
    can_edit = bool(
        ent["manual_editing"] and clip.user_id == user.id and clip.status == "candidate"
    )
    return {
        "clip": clip_ser(clip),
        "job": job_ser(job) if job is not None else None,
        "entitlements": ent,
        "caption_styles": _caption_style_options(user),
        "export_heights": export_heights_allowed(user),
        "can_edit": can_edit,
    }


@router.post("/clips/{clip_id}/edit", status_code=201)
async def api_clip_edit(
    request: Request,
    clip_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    if clip.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own clips.")
    data = await _json_body(request)
    job = db.get(ClipJob, clip.job_id)
    source = db.get(SourceVideo, clip.source_id)

    ent = entitlements_for(user)
    if not ent["manual_editing"]:
        return _err("Manual editing requires the Lite or Pro plan.")
    if clip.status != "candidate":
        return _err("Only candidate clips can be edited.")
    caption_style = str(data.get("caption_style") or "simple").strip()
    if not caption_style_allowed(user, caption_style):
        return _err("That caption style is not available on your plan — please upgrade to use it.")
    export_height_int = _as_int(data.get("export_height"), -1)
    if export_height_int is None or export_height_int < 0:
        return _err("Please choose a valid export height.")
    if export_height_int not in export_heights_allowed(user):
        return _err("That export height is not available on your plan — please upgrade to use it.")
    facecam_mode = str(data.get("facecam_mode") or "auto").strip()
    if facecam_mode not in FACE_CAM_MODES:
        return _err("Please choose a valid facecam mode.")
    if facecam_mode == "manual" and not ent["advanced_positioning"]:
        return _err("Manual facecam positioning requires the Pro plan.")
    start = _as_float(data.get("start_s"))
    end = _as_float(data.get("end_s"))
    if start is None or end is None:
        return _err("Start and end must be numbers (seconds).")
    if not (0 <= start < end):
        return _err("Start must be 0 or later and earlier than the end time.")
    if source is not None and source.duration_s is not None and end > source.duration_s:
        return _err("End time is after the end of the source video.")
    coords: dict[str, float | None] = {}
    for name in ("facecam_x", "facecam_y", "facecam_w", "facecam_h"):
        raw = data.get(name)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            coords[name] = None
        else:
            value = _as_float(raw)
            if value is None:
                return _err("Facecam position values must be numbers.")
            coords[name] = value
    if not _is_unlimited(user) and user.credits < 1:
        return _err(NO_CREDITS_MSG)

    new_job = ClipJob(
        user_id=user.id,
        source_id=clip.source_id,
        title=clip.title,
        status="queued",
        progress=0,
        log=f"Re-render of clip #{clip.id}: trim {start:g}s–{end:g}s.",
        caption_style=caption_style,
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
    return JSONResponse({"job": job_ser(new_job)}, status_code=201)


@router.post("/clips/{clip_id}/approve")
def api_clip_approve(
    request: Request,
    clip_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
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
    return {"clip": clip_ser(clip)}


@router.post("/clips/{clip_id}/reject")
def api_clip_reject(
    request: Request,
    clip_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    if clip.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only reject your own clips.")
    clip.status = "rejected"
    log_activity(db, user.id, "clip_rejected", f"Clip rejected: {clip.title}")
    db.commit()
    return {"clip": clip_ser(clip)}


@router.get("/clips/{clip_id}/preview")
def api_clip_preview(
    clip_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    clip = _get_clip(db, clip_id, user)
    if not clip.file_path or not Path(clip.file_path).is_file():
        raise HTTPException(status_code=404, detail="Clip file is missing.")
    media_type = "video/mp4" if Path(clip.file_path).suffix.lower() == ".mp4" else None
    return FileResponse(clip.file_path, media_type=media_type, filename=Path(clip.file_path).name)


@router.get("/clips/{clip_id}/thumb")
def api_clip_thumb(
    clip_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    clip = _get_clip(db, clip_id, user)
    if not clip.thumb_path or not Path(clip.thumb_path).is_file():
        raise HTTPException(status_code=404, detail="Thumbnail is missing.")
    suffix = Path(clip.thumb_path).suffix.lower()
    media_type = {".png": "image/png", ".jpg": "image/jpeg",
                  ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix)
    return FileResponse(clip.thumb_path, media_type=media_type, filename=Path(clip.thumb_path).name)


# --------------------------------------------------------------------------
# Downloads (approved only)
# --------------------------------------------------------------------------
@router.get("/downloads")
def api_downloads(user: User = Depends(api_require_user), db: Session = Depends(get_db)):
    clips = list(
        db.scalars(
            select(Clip)
            .where(Clip.user_id == user.id, Clip.status == "approved")
            .order_by(Clip.created_at.desc(), Clip.id.desc())
        )
    )
    return {"clips": [clip_ser(c) for c in clips]}


@router.get("/downloads/{clip_id}")
def api_download(
    clip_id: int,
    user: User = Depends(api_require_user),
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


# --------------------------------------------------------------------------
# Payments (NayaPay, manual)
# --------------------------------------------------------------------------
@router.get("/pay")
def api_pay(user: User = Depends(api_require_user), db: Session = Depends(get_db)):
    promotion = get_or_create_promotion(db)
    db.commit()
    promo_active = promotion_is_active(promotion)
    amounts = {}
    for plan_key in ("lite", "pro"):
        _promo, _active, amount, _is_promo = _pricing(db, plan_key)
        amounts[plan_key] = amount
    return {
        "plans": {
            "lite": {"price_pkr": PLANS["lite"]["price_pkr"]},
            "pro": {"price_pkr": PLANS["pro"]["price_pkr"]},
        },
        "promotion": promotion_ser(promotion),
        "promo_active": promo_active,
        "amounts": amounts,
        "qr_available": os.path.exists(NAYAPAY_QR_PATH),
        "account_holder": NAYAPAY_ACCOUNT_HOLDER,
        "number": NAYAPAY_NUMBER,
        "instruction": NAYAPAY_QR_INSTRUCTION,
    }


@router.get("/pay/qr")
def api_pay_qr(user: User = Depends(api_require_user)):
    if not os.path.exists(NAYAPAY_QR_PATH):
        return _err("QR code not yet provided by the owner.", 404)
    return FileResponse(NAYAPAY_QR_PATH)


@router.post("/pay", status_code=201)
async def api_pay_submit(
    request: Request,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    """Multipart payment submission — same server-side pricing, promo-seat
    reservation and screenshot handling as POST /pay in the HTML router.
    Submission NEVER activates a plan."""
    _require_csrf(request)
    check_rate_limit(request, "payment_submit", key=str(user.id))
    form = await request.form()
    plan = form.get("plan") if isinstance(form.get("plan"), str) else ""
    txn_id = (form.get("txn_id") or "").strip() if isinstance(form.get("txn_id"), str) else ""
    screenshot = form.get("screenshot")
    has_shot = screenshot is not None and getattr(screenshot, "filename", "")

    if plan not in ("lite", "pro"):
        return _err("Choose a valid plan (Lite or Pro).")
    if not txn_id:
        return _err("Enter the NayaPay transaction ID.")
    if len(txn_id) > 120:
        return _err("Transaction ID must be 120 characters or fewer.")
    if not has_shot:
        return _err("Upload a screenshot of your NayaPay payment.")
    if not is_allowed_image(screenshot.filename):
        return _err("Screenshot must be a PNG, JPG, JPEG or WebP image.")

    pay_dir = user_payment_dir(user.id)
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(dir=pay_dir, prefix=".pay_tmp_", delete=False)
        tmp_path = tmp.name
        total = 0
        limit = MAX_SCREENSHOT_MB * 1024 * 1024
        while True:
            chunk = await screenshot.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError("oversize")
            tmp.write(chunk)
        tmp.close()
    except ValueError:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return _err(f"Screenshot must be {MAX_SCREENSHOT_MB} MB or smaller.")

    _promotion, promo_active, amount, is_promotional = _pricing(db, plan)
    if plan == "pro" and promo_active:
        if not reserve_promo_seat(db):
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return _err("The Pro promotion seats are sold out.")

    payment = PaymentRequest(
        user_id=user.id,
        plan=plan,
        amount_pkr=amount,
        is_promotional=is_promotional,
        txn_id=txn_id,
        screenshot_path="",
        status="pending",
    )
    db.add(payment)
    try:
        db.flush()
        final_name = f"pay_{payment.id}_{safe_filename(screenshot.filename)}"
        final_path = pay_dir / final_name
        os.replace(tmp_path, final_path)
        tmp_path = None
        payment.screenshot_path = str(final_path)
        log_activity(
            db, user.id, "payment_submitted",
            f"{PLANS[plan]['name']} payment submitted (PKR {amount}).",
        )
        db.commit()
    except Exception:
        db.rollback()
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return _err("Something went wrong while saving your payment. Please try again.", 500)
    return JSONResponse({"payment": payment_ser(db, payment)}, status_code=201)


@router.get("/payments")
def api_payments(user: User = Depends(api_require_user), db: Session = Depends(get_db)):
    payments = db.scalars(
        select(PaymentRequest)
        .where(PaymentRequest.user_id == user.id)
        .order_by(PaymentRequest.id.desc())
    ).all()
    return {"payments": [payment_ser(db, p) for p in payments]}


@router.get("/pay/waiting/{payment_id}")
def api_pay_waiting(
    payment_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    payment = db.get(PaymentRequest, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found.")
    if payment.user_id != user.id and user.role != "owner":
        raise HTTPException(status_code=403, detail="You can only view your own payments.")
    return {"payment": payment_ser(db, payment)}


# --------------------------------------------------------------------------
# Support
# --------------------------------------------------------------------------
@router.get("/support")
def api_support_list(user: User = Depends(api_require_user), db: Session = Depends(get_db)):
    threads = db.scalars(
        select(SupportThread)
        .where(SupportThread.user_id == user.id)
        .order_by(SupportThread.updated_at.desc(), SupportThread.id.desc())
    ).all()
    return {"threads": [thread_ser(db, t) for t in threads]}


@router.post("/support", status_code=201)
async def api_support_create(
    request: Request,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    data = await _json_body(request)
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not subject:
        return _err("Enter a subject for your request.")
    if len(subject) > 200:
        return _err("Subject must be 200 characters or fewer.")
    if not body:
        return _err("Enter a message describing your issue.")
    if len(body) > 5000:
        return _err("Message must be 5000 characters or fewer.")
    thread = SupportThread(user_id=user.id, subject=subject, status="open")
    db.add(thread)
    db.flush()
    db.add(SupportMessage(
        thread_id=thread.id,
        author_id=user.id,
        author_role="owner" if user.role == "owner" else "customer",
        body=body,
    ))
    log_activity(db, user.id, "support_opened", f"Support request opened: {subject[:200]}")
    db.commit()
    return JSONResponse({"thread": thread_ser(db, thread)}, status_code=201)


@router.get("/support/{thread_id}")
def api_support_thread(
    thread_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    thread = _get_thread(db, thread_id, user)
    return {
        "thread": thread_ser(db, thread),
        "messages": [message_ser(m) for m in _thread_messages(db, thread.id)],
    }


@router.post("/support/{thread_id}/reply", status_code=201)
async def api_support_reply(
    request: Request,
    thread_id: int,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    thread = _get_thread(db, thread_id, user)
    data = await _json_body(request)
    body = str(data.get("body") or "").strip()
    if not body:
        return _err("Enter a reply message.")
    if len(body) > 5000:
        return _err("Message must be 5000 characters or fewer.")
    msg = SupportMessage(
        thread_id=thread.id,
        author_id=user.id,
        author_role="owner" if user.role == "owner" else "customer",
        body=body,
    )
    db.add(msg)
    if thread.status == "closed" and user.role != "owner":
        thread.status = "open"
    thread.updated_at = _utcnow()
    log_activity(db, user.id, "support_replied", f"Replied to support thread #{thread.id}.")
    db.commit()
    db.refresh(msg)
    return JSONResponse({"message": message_ser(msg)}, status_code=201)


# --------------------------------------------------------------------------
# YouTube monitoring (mutations are Pro-only)
# --------------------------------------------------------------------------
@router.get("/yt")
def api_yt(user: User = Depends(api_require_user), db: Session = Depends(get_db)):
    channels = db.scalars(
        select(YTChannel)
        .where(YTChannel.user_id == user.id)
        .order_by(YTChannel.id.desc())
    ).all()
    ent = entitlements_for(user)
    return {
        "configured": bool(YOUTUBE_API_KEY),
        "youtube_monitoring": bool(ent["youtube_live_monitoring"]),
        "channels": [yt_channel_ser(c) for c in channels],
    }


@router.post("/yt/channels", status_code=201)
async def api_yt_add(
    request: Request,
    user: User = Depends(api_require_pro),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    data = await _json_body(request)
    parsed = parse_channel_input(str(data.get("channel_url") or ""))
    if not parsed:
        return _err("Enter a valid YouTube channel URL or @handle.")
    channel = YTChannel(
        user_id=user.id,
        channel_url=parsed["channel_url"],
        channel_handle=parsed["handle"],
        channel_id=parsed["channel_id"],
        ingestion_state="waiting_authorized",
    )
    db.add(channel)
    db.flush()
    log_activity(
        db, user.id, "yt_channel_added",
        f"YouTube channel added: {parsed['handle'] or parsed['channel_id'] or parsed['channel_url']}",
    )
    db.commit()
    return JSONResponse({"channel": yt_channel_ser(channel)}, status_code=201)


def _own_channel(db: Session, channel_id: int, user: User) -> YTChannel:
    channel = db.get(YTChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="YouTube channel not found.")
    if channel.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only manage your own channels.")
    return channel


@router.post("/yt/channels/{channel_id}/check")
def api_yt_check(
    request: Request,
    channel_id: int,
    user: User = Depends(api_require_pro),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    channel = _own_channel(db, channel_id, user)
    result = check_live_status(channel)
    was_live = channel.is_live
    prev_live_video_id = channel.live_video_id or ""
    channel.last_checked_at = _utcnow()
    channel.last_status = result["status"]
    channel.is_live = result["is_live"]
    channel.live_video_id = result["live_video_id"]
    channel.live_title = result["live_title"]
    if result["status"] == "live" and not was_live:
        log_activity(db, user.id, "yt_live", f"Channel went live: {channel.live_title or channel.channel_url}")
    # LIVE-STREAM CLIP EXTRACTION hook (services/live_capture.py): when an
    # authorised channel is live this starts background segment capture;
    # when a stream just ended it queues the VOD for clipping. The hook
    # never raises and never changes the check result above.
    from ..services.live_capture import on_live_status_change

    on_live_status_change(db, channel, result,
                          prev_live_video_id=prev_live_video_id,
                          was_live=was_live)
    db.commit()
    if not result["configured"] or result["status"] == "error":
        # Honest failure: never invent a live status when the API key is
        # missing or the check failed.
        return _err(result["error"] or "YouTube live-status check failed.")
    return {"channel": yt_channel_ser(channel)}


@router.post("/yt/channels/{channel_id}/delete")
def api_yt_delete(
    request: Request,
    channel_id: int,
    user: User = Depends(api_require_pro),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    channel = _own_channel(db, channel_id, user)
    db.delete(channel)
    db.commit()
    return {"ok": True}


@router.post("/yt/channels/{channel_id}/authorize")
async def api_yt_authorize(
    request: Request,
    channel_id: int,
    user: User = Depends(api_require_pro),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    channel = _own_channel(db, channel_id, user)
    data = await _json_body(request)
    note = str(data.get("note") or "").strip()
    if len(note) > 500:
        return _err("Authorisation note must be 500 characters or fewer.")
    channel.ingestion_state = "authorized"
    channel.authorized_source_note = note
    db.commit()
    return {"channel": yt_channel_ser(channel)}


# --------------------------------------------------------------------------
# Live segments: moment scoring (the pipeline's own scorer, no new "AI")
# --------------------------------------------------------------------------
@router.get("/live/segments/{source_id}/moments")
def api_live_segment_moments(
    source_id: int,
    user: User = Depends(api_require_pro),
    db: Session = Depends(get_db),
):
    """Rank the clip-worthy moments in a finished live segment (or any
    finished source file) using the real pipeline scorer from
    ``pipeline/analyze.py`` — scene cuts + per-second audio energy.

    This creates no clips and no job: it previews what the pipeline
    would clip, so the owner can see the ranked candidates for a
    captured segment before (or instead of) queueing a job. Scores are
    the pipeline's own scores, with no transcript available the speech
    component is 0. Runs synchronously; meant for finished segments
    (<=10 min), not whole multi-hour VODs.
    """
    from ..services.live_capture import score_segment_file

    source = db.get(SourceVideo, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    if source.user_id != user.id and not user.is_owner:
        raise HTTPException(status_code=403, detail="You can only view your own sources.")
    if not source.stored_path or not Path(source.stored_path).is_file():
        return _err(
            "This source has no finished file on disk yet — the VOD has "
            "not been downloaded, or the live segment is still recording.",
            409,
        )
    try:
        scored = score_segment_file(source.stored_path)
    except FileNotFoundError:
        return _err("Source file is missing.", 404)
    except RuntimeError as exc:
        return _err(str(exc), 422)
    return {"source": source_ser(source), **scored}


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
@router.get("/settings")
def api_settings(user: User = Depends(api_require_user), db: Session = Depends(get_db)):
    ent = entitlements_for(user)
    allowed_styles = ent["caption_style_list"]
    allowed_heights = export_heights_allowed(user)
    stored_style = _get_pref(db, user.id, "caption_style", "simple")
    default_caption_style = stored_style if stored_style in allowed_styles else allowed_styles[0]
    try:
        stored_h = int(_get_pref(db, user.id, "export_height", "1280"))
    except ValueError:
        stored_h = 1280
    default_export_height = stored_h if stored_h in allowed_heights else allowed_heights[0]
    return {
        "user": user_summary(user),
        "default_caption_style": default_caption_style,
        "default_export_height": default_export_height,
        "caption_styles": _caption_style_options(user),
        "export_heights": allowed_heights,
    }


@router.post("/settings")
async def api_settings_update(
    request: Request,
    user: User = Depends(api_require_user),
    db: Session = Depends(get_db),
):
    _require_csrf(request)
    check_rate_limit(request, "generic_post", key=str(user.id))
    data = await _json_body(request)
    display_name = str(data.get("display_name", user.display_name) or "").strip()
    email = str(data.get("email", user.email) or "").strip().lower()
    default_caption_style = str(data.get("default_caption_style") or "simple").strip()
    export_height_int = _as_int(data.get("default_export_height"), 1280)
    if len(display_name) > 80:
        return _err("Display name must be 80 characters or fewer.")
    if not _EMAIL_RE.fullmatch(email):
        return _err("Please enter a valid email address.")
    existing = db.scalar(
        select(User).where(func.lower(User.email) == email, User.id != user.id)
    )
    if existing is not None:
        return _err("That email is already used by another account.")
    if not caption_style_allowed(user, default_caption_style):
        return _err("That caption style is not available on your plan.")
    if export_height_int not in export_heights_allowed(user):
        return _err("That export height is not available on your plan.")
    user.display_name = display_name
    user.email = email
    _set_pref(db, user.id, "caption_style", default_caption_style)
    _set_pref(db, user.id, "export_height", str(export_height_int))
    db.commit()
    return {"user": user_summary(user)}


# --------------------------------------------------------------------------
# Owner (role == owner only)
# --------------------------------------------------------------------------
def _audit(db: Session, actor: User, action: str, detail: str) -> None:
    db.add(AuditLog(actor_user_id=actor.id, action=action, detail=detail[:500]))


def _get_payment(db: Session, payment_id: int) -> PaymentRequest:
    payment = db.get(PaymentRequest, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment request not found.")
    return payment


def _get_target(db: Session, user_id: int) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return target


def _forbid_owner_target(target: User) -> None:
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="The owner account cannot be modified.")


def _owner_post_guards(request: Request, user: User) -> None:
    check_rate_limit(request, "generic_post", key=str(user.id))
    _require_csrf(request)


@router.get("/owner/overview")
def api_owner_overview(user: User = Depends(api_require_owner), db: Session = Depends(get_db)):
    counts = {
        "users": db.scalar(select(func.count(User.id)).where(User.role == "customer")) or 0,
        "pending_payments": db.scalar(
            select(func.count(PaymentRequest.id)).where(PaymentRequest.status == "pending")
        ) or 0,
        "open_support": db.scalar(
            select(func.count(SupportThread.id)).where(SupportThread.status == "open")
        ) or 0,
        "clips_total": db.scalar(select(func.count(Clip.id))) or 0,
        "jobs_queued": db.scalar(
            select(func.count(ClipJob.id)).where(ClipJob.status == "queued")
        ) or 0,
    }
    recent_payments = db.scalars(
        select(PaymentRequest).where(PaymentRequest.status == "pending")
        .order_by(PaymentRequest.id.desc()).limit(5)
    ).all()
    recent_activity = db.scalars(
        select(Activity).order_by(Activity.id.desc()).limit(10)
    ).all()
    return {
        "counts": counts,
        "recent_payments": [payment_ser(db, p) for p in recent_payments],
        "recent_activity": [activity_ser(a) for a in recent_activity],
    }


@router.get("/owner/payments")
def api_owner_payments(
    status: str = Query(default="pending"),
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    filter_status = status if status in ("pending", "approved", "rejected", "all") else "pending"
    query = select(PaymentRequest).order_by(PaymentRequest.id.desc())
    if filter_status != "all":
        query = query.where(PaymentRequest.status == filter_status)
    payments = db.scalars(query).all()
    return {"payments": [payment_ser(db, p) for p in payments]}


@router.get("/owner/payments/{payment_id}")
def api_owner_payment_detail(
    payment_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    payment = _get_payment(db, payment_id)
    payer = db.get(User, payment.user_id)
    return {
        "payment": payment_ser(db, payment),
        "user": user_public(payer) if payer is not None else None,
    }


@router.get("/owner/payments/{payment_id}/screenshot")
def api_owner_payment_screenshot(
    payment_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    payment = _get_payment(db, payment_id)
    if not payment.screenshot_path or not os.path.isfile(payment.screenshot_path):
        raise HTTPException(status_code=404, detail="Screenshot not found.")
    return FileResponse(payment.screenshot_path)


@router.post("/owner/payments/{payment_id}/approve")
def api_owner_payment_approve(
    request: Request,
    payment_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    payment = _get_payment(db, payment_id)
    try:
        apply_payment(db, payment, reviewer=user)
    except ValueError as exc:
        db.rollback()
        # Double approval is a conflict (409); a blocked plan change is a
        # validation failure (400). Both carry the billing message.
        if "already been" in str(exc):
            return _err(str(exc), 409)
        return _err(str(exc), 400)
    db.commit()
    db.refresh(payment)
    return {"payment": payment_ser(db, payment)}


@router.post("/owner/payments/{payment_id}/reject")
async def api_owner_payment_reject(
    request: Request,
    payment_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    payment = _get_payment(db, payment_id)
    data = await _json_body(request)
    note = str(data.get("note") or "")
    if len(note) > 500:
        return _err("Review note must be 500 characters or fewer.")
    try:
        reject_payment(db, payment, reviewer=user, note=note)
    except ValueError as exc:
        db.rollback()
        if "already been" in str(exc):
            return _err(str(exc), 409)
        return _err(str(exc), 400)
    db.commit()
    db.refresh(payment)
    return {"payment": payment_ser(db, payment)}


@router.get("/owner/users")
def api_owner_users(
    q: str = Query(default=""),
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    query = select(User).where(User.role == "customer")
    if q:
        like = f"%{q}%"
        query = query.where((User.username.ilike(like)) | (User.email.ilike(like)))
    users = db.scalars(query.order_by(User.id.desc())).all()
    return {"users": [user_public(u) for u in users]}


@router.get("/owner/users/{user_id}")
def api_owner_user_detail(
    user_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    target = _get_target(db, user_id)
    subscriptions = db.scalars(
        select(Subscription).where(Subscription.user_id == target.id)
        .order_by(Subscription.id.desc())
    ).all()
    ledger = db.scalars(
        select(CreditLedger).where(CreditLedger.user_id == target.id)
        .order_by(CreditLedger.id.desc()).limit(20)
    ).all()
    payments = db.scalars(
        select(PaymentRequest).where(PaymentRequest.user_id == target.id)
        .order_by(PaymentRequest.id.desc())
    ).all()
    return {
        "user": user_public(target),
        "subscriptions": [subscription_ser(s) for s in subscriptions],
        "ledger": [ledger_ser(row) for row in ledger],
        "payments": [payment_ser(db, p) for p in payments],
    }


@router.post("/owner/users/{user_id}/status")
async def api_owner_user_status(
    request: Request,
    user_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    data = await _json_body(request)
    is_active = _as_bool(data.get("is_active")) if "is_active" in data else None
    is_suspended = _as_bool(data.get("is_suspended")) if "is_suspended" in data else None
    if is_active is None and is_suspended is None:
        return _err("Provide is_active and/or is_suspended.")
    labels = []
    if is_active is not None:
        target.is_active = is_active
        if is_active:
            target.is_suspended = False
        labels.append("activated" if is_active else "deactivated")
    if is_suspended is not None:
        target.is_suspended = is_suspended
        labels.append("suspended" if is_suspended else "unsuspended")
    label = " and ".join(labels)
    _audit(db, user, "user_status_changed",
           f"User {target.username} (id={target.id}) {label} by owner.")
    log_activity(db, target.id, "account_updated", f"Your account was {label} by the owner.")
    db.commit()
    return {"user": user_public(target)}


@router.post("/owner/users/{user_id}/plan")
async def api_owner_user_plan(
    request: Request,
    user_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    data = await _json_body(request)
    plan = str(data.get("plan") or "")
    if plan not in PLANS:
        return _err("Unknown plan.")
    now = _utcnow()
    old_credits = target.credits
    old_plan = target.plan
    new_credits = PLANS[plan]["monthly_clips"]
    target.plan = plan
    target.credits = new_credits
    target.clips_used = 0
    target.plan_started_at = now
    target.plan_ends_at = billing.subscription_end(now) if plan in ("lite", "pro") else None
    db.add(CreditLedger(
        user_id=target.id,
        delta=new_credits - old_credits,
        reason=f"Owner plan change to {plan}",
        ref_type="owner_plan_change",
        ref_id=user.id,
    ))
    _audit(db, user, "user_plan_changed",
           f"User {target.username} (id={target.id}) plan changed {old_plan} -> {plan} by owner.")
    log_activity(db, target.id, "plan_changed",
                 f"Your plan was changed to {PLANS[plan]['name']} by the owner.")
    db.commit()
    return {"user": user_public(target)}


@router.post("/owner/users/{user_id}/credits")
async def api_owner_user_credits(
    request: Request,
    user_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    data = await _json_body(request)
    delta_int = _as_int(data.get("delta"))
    if delta_int is None:
        return _err("Credits change must be a whole number.")
    reason = str(data.get("reason") or "").strip()
    if not reason:
        return _err("A reason is required for a credits adjustment.")
    if len(reason) > 200:
        return _err("Reason must be 200 characters or fewer.")
    old_credits = target.credits
    target.credits = max(0, target.credits + delta_int)
    applied = target.credits - old_credits
    db.add(CreditLedger(user_id=target.id, delta=applied, reason=reason,
                        ref_type="owner_credit_adjustment", ref_id=user.id))
    _audit(db, user, "credits_adjusted",
           f"User {target.username} (id={target.id}) credits {old_credits} -> {target.credits} "
           f"(delta {applied}). Reason: {reason}")
    log_activity(db, target.id, "credits_adjusted",
                 f"Your credits were adjusted by {applied:+d} by the owner. New balance: {target.credits}.")
    db.commit()
    return {"user": user_public(target)}


@router.post("/owner/users/{user_id}/extend")
async def api_owner_user_extend(
    request: Request,
    user_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    data = await _json_body(request)
    days_int = _as_int(data.get("days"))
    if days_int is None:
        return _err("Days must be a whole number.")
    if not 1 <= days_int <= 365:
        return _err("Days must be between 1 and 365.")
    base = target.plan_ends_at or _utcnow()
    target.plan_ends_at = base + timedelta(days=days_int)
    _audit(db, user, "subscription_extended",
           f"User {target.username} (id={target.id}) plan extended by {days_int} days by owner.")
    log_activity(db, target.id, "subscription_extended",
                 f"Your plan was extended by {days_int} days by the owner.")
    db.commit()
    return {"user": user_public(target)}


@router.post("/owner/users/{user_id}/complimentary")
async def api_owner_user_complimentary(
    request: Request,
    user_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    data = await _json_body(request)
    enable = _as_bool(data.get("enable"))
    if enable is None:
        return _err("Complimentary setting must be true or false.")
    target.complimentary_premium = enable
    state = "granted" if enable else "revoked"
    _audit(db, user, "complimentary_premium_changed",
           f"User {target.username} (id={target.id}) complimentary premium {state} by owner.")
    log_activity(db, target.id, "account_updated",
                 f"Complimentary Premium was {state} for your account by the owner.")
    db.commit()
    return {"user": user_public(target)}


@router.get("/owner/support")
def api_owner_support(user: User = Depends(api_require_owner), db: Session = Depends(get_db)):
    threads = db.scalars(select(SupportThread)).all()
    threads.sort(key=lambda t: (
        0 if t.status == "open" else 1,
        -(t.created_at.timestamp() if t.created_at is not None else 0),
    ))
    return {"threads": [thread_ser(db, t) for t in threads]}


@router.get("/owner/support/{thread_id}")
def api_owner_support_thread(
    thread_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    thread = db.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Support thread not found.")
    return {
        "thread": thread_ser(db, thread),
        "messages": [message_ser(m) for m in _thread_messages(db, thread.id)],
    }


@router.post("/owner/support/{thread_id}/reply", status_code=201)
async def api_owner_support_reply(
    request: Request,
    thread_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    thread = db.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Support thread not found.")
    data = await _json_body(request)
    body = str(data.get("body") or "").strip()
    if not body:
        return _err("Reply message cannot be empty.")
    if len(body) > 5000:
        return _err("Message must be 5000 characters or fewer.")
    msg = SupportMessage(thread_id=thread.id, author_id=user.id,
                         author_role="owner", body=body)
    db.add(msg)
    thread.updated_at = _utcnow()
    _audit(db, user, "support_replied",
           f"Owner replied to support thread #{thread.id} ({thread.subject}).")
    log_activity(db, thread.user_id, "support_replied",
                 "The owner replied to your support request.")
    db.commit()
    db.refresh(msg)
    return JSONResponse({"message": message_ser(msg)}, status_code=201)


@router.post("/owner/support/{thread_id}/close")
def api_owner_support_close(
    request: Request,
    thread_id: int,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    thread = db.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Support thread not found.")
    thread.status = "closed"
    thread.updated_at = _utcnow()
    _audit(db, user, "support_thread_closed",
           f"Owner closed support thread #{thread.id} ({thread.subject}).")
    log_activity(db, thread.user_id, "support_closed",
                 "Your support request was closed by the owner.")
    db.commit()
    return {"thread": thread_ser(db, thread)}


@router.get("/owner/promotion")
def api_owner_promotion(user: User = Depends(api_require_owner), db: Session = Depends(get_db)):
    promotion = get_or_create_promotion(db)
    db.commit()
    return {
        "promotion": promotion_ser(promotion),
        "promo_active": promotion_is_active(promotion),
    }


@router.post("/owner/promotion")
async def api_owner_promotion_update(
    request: Request,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    promotion = get_or_create_promotion(db)
    data = await _json_body(request)
    if "max_seats" in data and data.get("max_seats") is not None:
        max_seats_int = _as_int(data.get("max_seats"))
        if max_seats_int is None:
            return _err("Max seats must be a whole number.")
        if not 0 <= max_seats_int <= 1000:
            return _err("Max seats must be between 0 and 1000.")
        floor = promotion.seats_reserved + promotion.seats_used
        promotion.max_seats = max(max_seats_int, floor)
    if "ends_at" in data:
        raw = data.get("ends_at")
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            promotion.ends_at = None
        else:
            try:
                ends_at_dt = datetime.fromisoformat(str(raw))
            except ValueError:
                return _err("End date/time is not a valid date.")
            if ends_at_dt.tzinfo is None:
                ends_at_dt = ends_at_dt.replace(tzinfo=timezone.utc)
            promotion.ends_at = ends_at_dt
    if "is_active" in data:
        active = _as_bool(data.get("is_active"))
        if active is None:
            return _err("is_active must be true or false.")
        promotion.is_active = active
    promotion.updated_at = _utcnow()
    _audit(db, user, "promotion_updated",
           f"Promotion updated: max_seats={promotion.max_seats}, "
           f"ends_at={promotion.ends_at}, is_active={promotion.is_active}.")
    db.commit()
    return {"promotion": promotion_ser(promotion)}


@router.get("/owner/system")
def api_owner_system(user: User = Depends(api_require_owner), db: Session = Depends(get_db)):
    now = _utcnow()
    if list_models is not None:
        models = [model_ser(m) for m in list_models(db)]
    else:
        models = []
    health = {
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "db_url_scheme": DATABASE_URL.split(":")[0],
        "worker": {
            "queued": db.scalar(select(func.count(ClipJob.id)).where(ClipJob.status == "queued")) or 0,
            "processing": db.scalar(
                select(func.count(ClipJob.id)).where(ClipJob.status.in_(_PROCESSING_STATUSES))
            ) or 0,
            "failed": db.scalar(select(func.count(ClipJob.id)).where(ClipJob.status == "failed")) or 0,
            "done_last_24h": db.scalar(
                select(func.count(ClipJob.id)).where(
                    ClipJob.status == "done",
                    ClipJob.updated_at >= now - timedelta(hours=24),
                )
            ) or 0,
        },
    }
    return {"health": health, "models": models, "storage": storage_stats()}


@router.post("/owner/system/models/{name}/install", status_code=202)
def api_owner_model_install(
    request: Request,
    name: str,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    if install_model is None:
        return _err("Model manager is not available yet.")
    try:
        result = install_model(db, name)
    except ValueError as exc:
        return _err(str(exc))
    _audit(db, user, "model_install_started", f"Model install started: {name}.")
    db.commit()
    model = {
        "name": result.get("name", name),
        "version": "",
        "size_mb": 0,
        "license": "",
        "status": result.get("status", "installing"),
        "error": "",
        "installed_at": None,
    }
    if list_models is not None:
        for entry in list_models(db):
            if entry.get("name") == name:
                model = model_ser(entry)
                break
    return JSONResponse({"model": model}, status_code=202)


@router.get("/owner/settings")
def api_owner_settings(user: User = Depends(api_require_owner), db: Session = Depends(get_db)):
    settings = {
        row.key: row.value
        for row in db.scalars(select(AppSetting)).all()
        if not _SECRET_KEY_RE.search(row.key)
    }
    return {"settings": settings}


@router.post("/owner/settings")
async def api_owner_settings_update(
    request: Request,
    user: User = Depends(api_require_owner),
    db: Session = Depends(get_db),
):
    _owner_post_guards(request, user)
    data = await _json_body(request)
    key = str(data.get("key") or "").strip()
    value = data.get("value")
    if not key:
        return _err("A setting key is required.")
    if len(key) > 80:
        return _err("Setting key must be 80 characters or fewer.")
    if _SECRET_KEY_RE.search(key):
        return _err("That setting key is not allowed via the API.")
    if value is None:
        return _err("A setting value is required.")
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=str(value)))
    else:
        row.value = str(value)
        row.updated_at = _utcnow()
    _audit(db, user, "settings_updated", f"Owner updated setting: {key}.")
    db.commit()
    return {"ok": True}
