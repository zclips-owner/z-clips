"""Payment routes: manual NayaPay payment submission.

A customer picks a plan, pays via NayaPay (QR code or account number) and
submits the transaction ID plus a screenshot. Nothing here verifies a
payment automatically — a PaymentRequest is created as ``pending`` and the
owner approves or rejects it (see app/routers/owner.py).
"""
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..activity import log_activity
from ..config import MAX_SCREENSHOT_MB, NAYAPAY_QR_INSTRUCTION, NAYAPAY_QR_PATH
from ..database import get_db
from ..deps import base_context, check_rate_limit, require_user, validate_csrf
from ..models import PaymentRequest
from ..plans import PLANS, promotion_is_active, promotion_price_for
from ..render import render
from ..services import billing
from ..storage import is_allowed_image, safe_filename, user_payment_dir

router = APIRouter()

_CHUNK_SIZE = 1024 * 1024  # 1 MB chunks while streaming the screenshot


def _pricing(db: Session, plan: str) -> tuple:
    """Return (promotion, promo_active, amount_pkr, is_promotional)."""
    promotion = billing.get_or_create_promotion(db)
    promo_active = promotion_is_active(promotion)
    promo_price = promotion_price_for(plan, promotion)
    if promo_price is not None:
        return promotion, promo_active, promo_price, True
    return promotion, promo_active, PLANS[plan]["price_pkr"], False


def _render_pay(request: Request, db: Session, user, plan: str, error: str | None, status_code: int = 200):
    """Render pay.html. ``plan`` falls back to lite for display purposes
    when the submitted value is not a payable plan."""
    display_plan = plan if plan in ("lite", "pro") else "lite"
    _promotion, promo_active, amount, is_promotional = _pricing(db, display_plan)
    return render(
        request,
        "pay.html",
        {
            **base_context(request, user),
            "plan": display_plan,
            "plan_info": PLANS[display_plan],
            "amount_pkr": amount,
            "is_promotional": is_promotional,
            "promo_active": promo_active,
            "promotion": _promotion,
            "qr_available": os.path.exists(NAYAPAY_QR_PATH),
            "qr_instruction": NAYAPAY_QR_INSTRUCTION,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/pay/qr")
def pay_qr(user=Depends(require_user)):
    """Serve the owner-supplied NayaPay QR image.

    The QR file lives at NAYAPAY_QR_PATH (storage/nayapay-qr.png by
    default) — it is NOT a static asset, so it is served here, only to
    signed-in users, and only when the owner has actually provided one.
    """
    if not os.path.exists(NAYAPAY_QR_PATH):
        raise HTTPException(status_code=404, detail="NayaPay QR code not yet provided.")
    return FileResponse(NAYAPAY_QR_PATH)


@router.get("/pay")
def pay_page(
    request: Request,
    plan: str = "lite",
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    # Free has no payment page.
    if plan not in ("lite", "pro"):
        return RedirectResponse("/plans", status_code=303)
    _promotion, promo_active, amount, is_promotional = _pricing(db, plan)
    db.commit()  # persist the promotion row if get_or_create_promotion created it
    return render(
        request,
        "pay.html",
        {
            **base_context(request, user),
            "plan": plan,
            "plan_info": PLANS[plan],
            "amount_pkr": amount,
            "is_promotional": is_promotional,
            "promo_active": promo_active,
            "promotion": _promotion,
            "qr_available": os.path.exists(NAYAPAY_QR_PATH),
            "qr_instruction": NAYAPAY_QR_INSTRUCTION,
            "error": None,
        },
    )


@router.post("/pay")
async def submit_payment(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user),
    plan: str = Form(...),
    txn_id: str = Form(...),
    csrf_token: str = Form(...),
    screenshot: UploadFile | None = File(None),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "payment_submit", key=str(user.id))

    txn_id = (txn_id or "").strip()
    if plan not in ("lite", "pro"):
        return _render_pay(request, db, user, plan, "Choose a valid plan (Lite or Pro).", status_code=400)
    if not txn_id:
        return _render_pay(request, db, user, plan, "Enter the NayaPay transaction ID.", status_code=400)
    if len(txn_id) > 120:
        return _render_pay(
            request, db, user, plan, "Transaction ID must be 120 characters or fewer.", status_code=400
        )
    if screenshot is None or not screenshot.filename:
        return _render_pay(
            request, db, user, plan, "Upload a screenshot of your NayaPay payment.", status_code=400
        )
    if not is_allowed_image(screenshot.filename):
        return _render_pay(
            request, db, user, plan, "Screenshot must be a PNG, JPG, JPEG or WebP image.", status_code=400
        )

    # Stream the screenshot to a temp file in the user's payment directory,
    # enforcing the size limit as we go (nothing is kept when oversize).
    pay_dir = user_payment_dir(user.id)
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(dir=pay_dir, prefix=".pay_tmp_", delete=False)
        tmp_path = tmp.name
        total = 0
        limit = MAX_SCREENSHOT_MB * 1024 * 1024
        while True:
            chunk = await screenshot.read(_CHUNK_SIZE)
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
        return _render_pay(
            request, db, user, plan,
            f"Screenshot must be {MAX_SCREENSHOT_MB} MB or smaller.", status_code=400,
        )

    _promotion, promo_active, amount, is_promotional = _pricing(db, plan)

    # Reserve a promo seat BEFORE creating the payment. If no seat is
    # available the payment is not created and the screenshot is discarded.
    if plan == "pro" and promo_active:
        if not billing.reserve_promo_seat(db):
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return _render_pay(
                request, db, user, plan,
                "The Pro promotion seats are sold out.", status_code=400,
            )

    # Create the PaymentRequest first so its id can name the screenshot file.
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
        return _render_pay(
            request, db, user, plan,
            "Something went wrong while saving your payment. Please try again.",
            status_code=500,
        )
    return RedirectResponse(f"/pay/waiting/{payment.id}", status_code=303)


@router.get("/pay/waiting/{payment_id}")
def pay_waiting(
    request: Request,
    payment_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    payment = db.get(PaymentRequest, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found.")
    if payment.user_id != user.id and user.role != "owner":
        raise HTTPException(status_code=403, detail="You can only view your own payments.")
    return render(
        request,
        "pay_waiting.html",
        {
            **base_context(request, user),
            "payment": payment,
            "plan_info": PLANS.get(payment.plan, PLANS["free"]),
        },
    )


@router.get("/payments")
def payment_history(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    payments = db.scalars(
        select(PaymentRequest)
        .where(PaymentRequest.user_id == user.id)
        .order_by(PaymentRequest.id.desc())
    ).all()
    return render(
        request,
        "payments.html",
        {**base_context(request, user), "payments": payments},
    )
