"""Owner (admin) routes for Z CLIPS.

Every route requires the owner role (``deps.require_owner``). Every POST
validates the CSRF token and the ``generic_post`` rate-limit bucket, and
every action writes an AuditLog row (plus an Activity row where a user is
affected). Renders follow docs/ROUTE_CONTEXT_SPEC.md exactly.
"""
import os
import shutil
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..activity import log_activity
from ..config import DATABASE_URL, MAX_SCREENSHOT_MB, NAYAPAY_QR_PATH
from ..database import get_db
from ..deps import base_context, check_rate_limit, require_owner, validate_csrf
from ..models import (
    Activity,
    AppSetting,
    AuditLog,
    Clip,
    ClipJob,
    CreditLedger,
    PaymentRequest,
    Subscription,
    SupportMessage,
    SupportThread,
    User,
)
from ..plans import PLANS, promotion_is_active, subscription_end
from ..render import render
from ..services import billing
from ..storage import is_allowed_image, storage_stats

# models_manager is being written in parallel by another agent. Import it
# defensively: when it is not there yet the system page still renders
# (with models=[] and a note) instead of breaking the whole router.
try:
    from ..services.models_manager import install_model, list_models
except ImportError:  # pragma: no cover - module not installed yet
    install_model = None
    list_models = None

router = APIRouter()

# ClipJob statuses that count as "processing" (in-flight work).
_PROCESSING_STATUSES = ("analyzing", "rendering", "qc")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, actor: User, action: str, detail: str) -> None:
    db.add(AuditLog(actor_user_id=actor.id, action=action, detail=detail[:500]))


def _post_guards(request: Request, user: User, csrf_token: str) -> None:
    """CSRF + rate limit, required on every owner POST."""
    check_rate_limit(request, "generic_post", key=str(user.id))
    validate_csrf(request, csrf_token)


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
    """The owner account can be viewed but never modified via admin actions."""
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="The owner account cannot be modified.")


def _payment_detail_ctx(request: Request, user: User, db: Session, payment: PaymentRequest,
                        error: str | None = None) -> dict:
    payer = db.get(User, payment.user_id)
    classification = billing.classify_payment(payer, payment.plan) if payer is not None else "first_purchase"
    return {
        **base_context(request, user),
        "payment": payment,
        "payer": payer,
        "classification": classification,
        "plan_change_blocked": classification == "plan_change",
        "screenshot_url": f"/owner/payments/{payment.id}/screenshot" if payment.screenshot_path else None,
        "error": error,
    }


def _user_detail_ctx(request: Request, user: User, db: Session, target: User,
                     error: str | None = None, message: str | None = None) -> dict:
    return {
        **base_context(request, user),
        "target": target,
        "subscriptions": db.scalars(
            select(Subscription).where(Subscription.user_id == target.id)
            .order_by(Subscription.id.desc())
        ).all(),
        "ledger": db.scalars(
            select(CreditLedger).where(CreditLedger.user_id == target.id)
            .order_by(CreditLedger.id.desc()).limit(20)
        ).all(),
        "payments": db.scalars(
            select(PaymentRequest).where(PaymentRequest.user_id == target.id)
            .order_by(PaymentRequest.id.desc())
        ).all(),
        "activity": db.scalars(
            select(Activity).where(Activity.user_id == target.id)
            .order_by(Activity.id.desc()).limit(10)
        ).all(),
        "message": message,
        "error": error,
    }


def _system_ctx(request: Request, user: User, db: Session,
                error: str | None = None, message: str | None = None) -> dict:
    now = _now()
    if list_models is not None:
        models = list_models(db)
        note = message
    else:
        models = []
        note = message or "Model manager module is being installed."
    return {
        **base_context(request, user),
        "storage": storage_stats(),
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
        "models": models,
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "db_url_scheme": DATABASE_URL.split(":")[0],
        "message": note,
        "error": error,
    }


def _promotion_ctx(request: Request, user: User, db: Session, promotion,
                   error: str | None = None, message: str | None = None) -> dict:
    return {
        **base_context(request, user),
        "promotion": promotion,
        "promo_active": promotion_is_active(promotion),
        "message": message,
        "error": error,
    }


def _settings_ctx(request: Request, user: User, db: Session,
                  error: str | None = None, message: str | None = None) -> dict:
    return {
        **base_context(request, user),
        "settings": {row.key: row.value for row in db.scalars(select(AppSetting)).all()},
        "qr_available": os.path.exists(NAYAPAY_QR_PATH),
        "message": message,
        "error": error,
    }


def _upsert_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = _now()


# --------------------------------------------------------------------------
# Owner dashboard
# --------------------------------------------------------------------------
@router.get("/owner")
def owner_dashboard(request: Request, user: User = Depends(require_owner),
                    db: Session = Depends(get_db)):
    stats = {
        "users": db.scalar(select(func.count(User.id)).where(User.role == "customer")) or 0,
        "active": db.scalar(
            select(func.count(User.id)).where(
                User.role == "customer", User.is_active.is_(True), User.is_suspended.is_(False)
            )
        ) or 0,
        "suspended": db.scalar(
            select(func.count(User.id)).where(User.role == "customer", User.is_suspended.is_(True))
        ) or 0,
        "active_subscriptions": db.scalar(
            select(func.count(Subscription.id)).where(Subscription.status == "active")
        ) or 0,
        "pending_payments": db.scalar(
            select(func.count(PaymentRequest.id)).where(PaymentRequest.status == "pending")
        ) or 0,
        "approved_payments": db.scalar(
            select(func.count(PaymentRequest.id)).where(PaymentRequest.status == "approved")
        ) or 0,
        "revenue_pkr": db.scalar(
            select(func.coalesce(func.sum(PaymentRequest.amount_pkr), 0)).where(
                PaymentRequest.status == "approved"
            )
        ) or 0,
        "clips_total": db.scalar(select(func.count(Clip.id))) or 0,
        "jobs_queued": db.scalar(
            select(func.count(ClipJob.id)).where(ClipJob.status == "queued")
        ) or 0,
        "jobs_processing": db.scalar(
            select(func.count(ClipJob.id)).where(ClipJob.status.in_(_PROCESSING_STATUSES))
        ) or 0,
    }
    recent_payments = db.scalars(
        select(PaymentRequest).where(PaymentRequest.status == "pending")
        .order_by(PaymentRequest.id.desc()).limit(5)
    ).all()
    recent_activity = db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(10)
    ).all()
    return render(request, "owner.html", {
        **base_context(request, user),
        "user": user,
        "stats": stats,
        "recent_payments": recent_payments,
        "recent_activity": recent_activity,
    })


# --------------------------------------------------------------------------
# Payment requests
# --------------------------------------------------------------------------
@router.get("/owner/payments")
def owner_payments(request: Request, status: str = "pending",
                   user: User = Depends(require_owner), db: Session = Depends(get_db)):
    filter_status = status if status in ("pending", "approved", "rejected", "all") else "pending"
    query = select(PaymentRequest).order_by(PaymentRequest.id.desc())
    if filter_status != "all":
        query = query.where(PaymentRequest.status == filter_status)
    payments = db.scalars(query).all()
    return render(request, "owner_payments.html", {
        **base_context(request, user),
        "payments": payments,
        "filter_status": filter_status,
    })


@router.get("/owner/payments/{payment_id}")
def owner_payment_detail(request: Request, payment_id: int,
                         user: User = Depends(require_owner), db: Session = Depends(get_db)):
    payment = _get_payment(db, payment_id)
    return render(request, "owner_payment_detail.html",
                  _payment_detail_ctx(request, user, db, payment))


@router.get("/owner/payments/{payment_id}/screenshot")
def owner_payment_screenshot(request: Request, payment_id: int,
                             user: User = Depends(require_owner), db: Session = Depends(get_db)):
    payment = _get_payment(db, payment_id)
    if not payment.screenshot_path or not os.path.isfile(payment.screenshot_path):
        raise HTTPException(status_code=404, detail="Screenshot not found.")
    return FileResponse(payment.screenshot_path)


@router.post("/owner/payments/{payment_id}/approve")
def owner_payment_approve(request: Request, payment_id: int,
                          csrf_token: str = Form(...),
                          user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    payment = _get_payment(db, payment_id)
    try:
        billing.apply_payment(db, payment, reviewer=user)
    except ValueError as exc:
        db.rollback()
        payment = _get_payment(db, payment_id)
        return render(request, "owner_payment_detail.html",
                      _payment_detail_ctx(request, user, db, payment, error=str(exc)),
                      status_code=400)
    db.commit()
    return RedirectResponse(f"/owner/payments/{payment.id}", status_code=303)


@router.post("/owner/payments/{payment_id}/reject")
def owner_payment_reject(request: Request, payment_id: int,
                         note: str = Form(""), csrf_token: str = Form(...),
                         user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    payment = _get_payment(db, payment_id)
    if len(note) > 500:
        return render(request, "owner_payment_detail.html",
                      _payment_detail_ctx(request, user, db, payment,
                                          error="Review note must be 500 characters or fewer."),
                      status_code=400)
    try:
        billing.reject_payment(db, payment, reviewer=user, note=note)
    except ValueError as exc:
        db.rollback()
        payment = _get_payment(db, payment_id)
        return render(request, "owner_payment_detail.html",
                      _payment_detail_ctx(request, user, db, payment, error=str(exc)),
                      status_code=400)
    db.commit()
    return RedirectResponse("/owner/payments", status_code=303)


# --------------------------------------------------------------------------
# User management
# --------------------------------------------------------------------------
@router.get("/owner/users")
def owner_users(request: Request, q: str = "",
                user: User = Depends(require_owner), db: Session = Depends(get_db)):
    query = select(User).where(User.role == "customer")
    if q:
        like = f"%{q}%"
        query = query.where((User.username.ilike(like)) | (User.email.ilike(like)))
    users = db.scalars(query.order_by(User.id.desc())).all()
    stats = {
        "total": db.scalar(select(func.count(User.id)).where(User.role == "customer")) or 0,
        "active": db.scalar(
            select(func.count(User.id)).where(
                User.role == "customer", User.is_active.is_(True), User.is_suspended.is_(False)
            )
        ) or 0,
        "suspended": db.scalar(
            select(func.count(User.id)).where(User.role == "customer", User.is_suspended.is_(True))
        ) or 0,
    }
    return render(request, "owner_users.html", {
        **base_context(request, user),
        "users": users,
        "q": q,
        "stats": stats,
    })


@router.get("/owner/users/{user_id}")
def owner_user_detail(request: Request, user_id: int,
                      user: User = Depends(require_owner), db: Session = Depends(get_db)):
    target = _get_target(db, user_id)
    return render(request, "owner_user_detail.html",
                  _user_detail_ctx(request, user, db, target))


@router.post("/owner/users/{user_id}/status")
def owner_user_status(request: Request, user_id: int,
                      action: str = Form(...), csrf_token: str = Form(...),
                      user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    if action == "activate":
        target.is_active = True
        target.is_suspended = False
        label = "activated"
    elif action == "deactivate":
        target.is_active = False
        label = "deactivated"
    elif action == "suspend":
        target.is_suspended = True
        label = "suspended"
    elif action == "unsuspend":
        target.is_suspended = False
        label = "unsuspended"
    else:
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="Unknown status action."),
                      status_code=400)
    _audit(db, user, "user_status_changed",
           f"User {target.username} (id={target.id}) {label} by owner.")
    log_activity(db, target.id, "account_updated", f"Your account was {label} by the owner.")
    db.commit()
    return RedirectResponse(f"/owner/users/{target.id}", status_code=303)


@router.post("/owner/users/{user_id}/plan")
def owner_user_plan(request: Request, user_id: int,
                    plan: str = Form(...), confirm: str = Form(""),
                    csrf_token: str = Form(...),
                    user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    if confirm != "yes":
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="Tick the confirmation to change this plan."),
                      status_code=400)
    if plan not in PLANS:
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="Unknown plan."),
                      status_code=400)
    now = _now()
    old_credits = target.credits
    old_plan = target.plan
    new_credits = PLANS[plan]["monthly_clips"]
    target.plan = plan
    target.credits = new_credits
    target.clips_used = 0
    target.plan_started_at = now
    target.plan_ends_at = subscription_end(now) if plan in ("lite", "pro") else None
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
    return RedirectResponse(f"/owner/users/{target.id}", status_code=303)


@router.post("/owner/users/{user_id}/credits")
def owner_user_credits(request: Request, user_id: int,
                       delta: str = Form(...), reason: str = Form(""),
                       csrf_token: str = Form(...),
                       user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    try:
        delta_int = int(delta)
    except (TypeError, ValueError):
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="Credits change must be a whole number."),
                      status_code=400)
    reason = (reason or "").strip()
    if not reason:
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="A reason is required for a credits adjustment."),
                      status_code=400)
    if len(reason) > 200:
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="Reason must be 200 characters or fewer."),
                      status_code=400)
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
    return RedirectResponse(f"/owner/users/{target.id}", status_code=303)


@router.post("/owner/users/{user_id}/extend")
def owner_user_extend(request: Request, user_id: int,
                      days: str = Form(...), csrf_token: str = Form(...),
                      user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    try:
        days_int = int(days)
    except (TypeError, ValueError):
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="Days must be a whole number."),
                      status_code=400)
    if not 1 <= days_int <= 365:
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="Days must be between 1 and 365."),
                      status_code=400)
    base = target.plan_ends_at or _now()
    target.plan_ends_at = base + timedelta(days=days_int)
    _audit(db, user, "subscription_extended",
           f"User {target.username} (id={target.id}) plan extended by {days_int} days by owner.")
    log_activity(db, target.id, "subscription_extended",
                 f"Your plan was extended by {days_int} days by the owner.")
    db.commit()
    return RedirectResponse(f"/owner/users/{target.id}", status_code=303)


@router.post("/owner/users/{user_id}/complimentary")
def owner_user_complimentary(request: Request, user_id: int,
                             enable: str = Form(...), csrf_token: str = Form(...),
                             user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    target = _get_target(db, user_id)
    _forbid_owner_target(target)
    if enable not in ("on", "off"):
        return render(request, "owner_user_detail.html",
                      _user_detail_ctx(request, user, db, target,
                                       error="Complimentary setting must be on or off."),
                      status_code=400)
    target.complimentary_premium = (enable == "on")
    state = "granted" if target.complimentary_premium else "revoked"
    _audit(db, user, "complimentary_premium_changed",
           f"User {target.username} (id={target.id}) complimentary premium {state} by owner.")
    log_activity(db, target.id, "account_updated",
                 f"Complimentary Premium was {state} for your account by the owner.")
    db.commit()
    return RedirectResponse(f"/owner/users/{target.id}", status_code=303)


# --------------------------------------------------------------------------
# Support inbox
# --------------------------------------------------------------------------
@router.get("/owner/support")
def owner_support(request: Request, user: User = Depends(require_owner),
                  db: Session = Depends(get_db)):
    threads = db.scalars(select(SupportThread)).all()
    threads.sort(key=lambda t: (
        0 if t.status == "open" else 1,
        -(t.created_at.timestamp() if t.created_at is not None else 0),
    ))
    return render(request, "owner_support.html", {
        **base_context(request, user),
        "threads": threads,
    })


@router.get("/owner/support/{thread_id}")
def owner_support_thread(request: Request, thread_id: int,
                         user: User = Depends(require_owner), db: Session = Depends(get_db)):
    thread = db.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Support thread not found.")
    messages = db.scalars(
        select(SupportMessage).where(SupportMessage.thread_id == thread.id)
        .order_by(SupportMessage.id.asc())
    ).all()
    return render(request, "support_thread.html", {
        **base_context(request, user),
        "thread": thread,
        "messages": messages,
        "error": None,
    })


@router.post("/owner/support/{thread_id}/reply")
def owner_support_reply(request: Request, thread_id: int,
                        message: str = Form(""), csrf_token: str = Form(...),
                        user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    thread = db.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Support thread not found.")
    body = (message or "").strip()
    if not body:
        messages = db.scalars(
            select(SupportMessage).where(SupportMessage.thread_id == thread.id)
            .order_by(SupportMessage.id.asc())
        ).all()
        return render(request, "support_thread.html", {
            **base_context(request, user),
            "thread": thread,
            "messages": messages,
            "error": "Reply message cannot be empty.",
        }, status_code=400)
    db.add(SupportMessage(thread_id=thread.id, author_id=user.id,
                          author_role="owner", body=body))
    thread.updated_at = _now()
    _audit(db, user, "support_replied",
           f"Owner replied to support thread #{thread.id} ({thread.subject}).")
    log_activity(db, thread.user_id, "support_replied",
                 "The owner replied to your support request.")
    db.commit()
    return RedirectResponse(f"/owner/support/{thread.id}", status_code=303)


@router.post("/owner/support/{thread_id}/close")
def owner_support_close(request: Request, thread_id: int,
                        csrf_token: str = Form(...),
                        user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    thread = db.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Support thread not found.")
    thread.status = "closed"
    thread.updated_at = _now()
    _audit(db, user, "support_thread_closed",
           f"Owner closed support thread #{thread.id} ({thread.subject}).")
    log_activity(db, thread.user_id, "support_closed",
                 "Your support request was closed by the owner.")
    db.commit()
    return RedirectResponse("/owner/support", status_code=303)


# --------------------------------------------------------------------------
# Promotion
# --------------------------------------------------------------------------
@router.get("/owner/promotion")
def owner_promotion(request: Request, user: User = Depends(require_owner),
                    db: Session = Depends(get_db)):
    promotion = billing.get_or_create_promotion(db)
    db.commit()
    return render(request, "owner_promotion.html",
                  _promotion_ctx(request, user, db, promotion))


@router.post("/owner/promotion")
def owner_promotion_update(request: Request,
                           max_seats: str = Form(...), ends_at: str = Form(""),
                           is_active: str | None = Form(None),
                           csrf_token: str = Form(...),
                           user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    promotion = billing.get_or_create_promotion(db)
    try:
        max_seats_int = int(max_seats)
    except (TypeError, ValueError):
        return render(request, "owner_promotion.html",
                      _promotion_ctx(request, user, db, promotion,
                                     error="Max seats must be a whole number."),
                      status_code=400)
    if not 0 <= max_seats_int <= 1000:
        return render(request, "owner_promotion.html",
                      _promotion_ctx(request, user, db, promotion,
                                     error="Max seats must be between 0 and 1000."),
                      status_code=400)
    ends_at_dt = None
    ends_at_raw = (ends_at or "").strip()
    if ends_at_raw:
        try:
            ends_at_dt = datetime.fromisoformat(ends_at_raw)
        except ValueError:
            return render(request, "owner_promotion.html",
                          _promotion_ctx(request, user, db, promotion,
                                         error="End date/time is not a valid date."),
                          status_code=400)
        if ends_at_dt.tzinfo is None:
            ends_at_dt = ends_at_dt.replace(tzinfo=timezone.utc)
    floor = promotion.seats_reserved + promotion.seats_used
    promotion.max_seats = max(max_seats_int, floor)
    promotion.ends_at = ends_at_dt
    promotion.is_active = (is_active == "on")
    promotion.updated_at = _now()
    _audit(db, user, "promotion_updated",
           f"Promotion updated: max_seats={promotion.max_seats}, "
           f"ends_at={promotion.ends_at}, is_active={promotion.is_active}.")
    db.commit()
    return RedirectResponse("/owner/promotion", status_code=303)


# --------------------------------------------------------------------------
# System & models
# --------------------------------------------------------------------------
@router.get("/owner/system")
def owner_system(request: Request, user: User = Depends(require_owner),
                 db: Session = Depends(get_db)):
    return render(request, "owner_system.html", _system_ctx(request, user, db))


@router.post("/owner/system/models/{name}/install")
def owner_model_install(request: Request, name: str,
                        csrf_token: str = Form(...),
                        user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    if install_model is None:
        return render(request, "owner_system.html",
                      _system_ctx(request, user, db,
                                  error="Model manager is not available yet."),
                      status_code=400)
    try:
        install_model(db, name)
    except ValueError as exc:
        return render(request, "owner_system.html",
                      _system_ctx(request, user, db, error=str(exc)),
                      status_code=400)
    _audit(db, user, "model_install_started", f"Model install started: {name}.")
    db.commit()
    return RedirectResponse("/owner/system", status_code=303)


# --------------------------------------------------------------------------
# Global settings
# --------------------------------------------------------------------------
@router.get("/owner/settings")
def owner_settings(request: Request, user: User = Depends(require_owner),
                   db: Session = Depends(get_db)):
    return render(request, "owner_settings.html", _settings_ctx(request, user, db))


@router.post("/owner/settings")
def owner_settings_update(request: Request,
                          support_email: str = Form(""),
                          announcement: str = Form(""),
                          qr: UploadFile | None = File(None),
                          csrf_token: str = Form(...),
                          user: User = Depends(require_owner), db: Session = Depends(get_db)):
    _post_guards(request, user, csrf_token)
    _upsert_setting(db, "support_email", (support_email or "").strip())
    _upsert_setting(db, "announcement", (announcement or "").strip())
    if qr is not None and qr.filename:
        if not is_allowed_image(qr.filename):
            db.rollback()
            return render(request, "owner_settings.html",
                          _settings_ctx(request, user, db,
                                        error="QR code must be a PNG, JPG, JPEG or WebP image."),
                          status_code=400)
        max_bytes = MAX_SCREENSHOT_MB * 1024 * 1024
        total = 0
        chunks: list[bytes] = []
        while True:
            chunk = qr.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                db.rollback()
                return render(request, "owner_settings.html",
                              _settings_ctx(request, user, db,
                                            error=f"QR code must be {MAX_SCREENSHOT_MB} MB or smaller."),
                              status_code=400)
            chunks.append(chunk)
        qr_path = NAYAPAY_QR_PATH
        os.makedirs(os.path.dirname(qr_path), exist_ok=True)
        with open(qr_path, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
    _audit(db, user, "settings_updated", "Owner updated global settings.")
    db.commit()
    return RedirectResponse("/owner/settings", status_code=303)
