"""Billing: subscriptions, credit carry-forward, promotion seat locking and
manual NayaPay payment approval.

Rules (from the master prompt, implemented exactly):
- Renewing the SAME plan before expiry: unused credits carry forward, new
  plan credits are added, validity restarts for 30 days. Transactional,
  no double crediting (a payment can only be approved once).
- Plan CHANGE behaviour is NOT invented here. If a payment is for a
  different plan while the user holds an active paid subscription, approval
  is refused and the owner must first change the plan explicitly in Admin
  User Management (audited), after which the payment can be approved as a
  same-plan payment. See ``classify_payment``.
- Promotion seats are reserved atomically at payment-submit time and
  converted to used on approval / released on rejection.
- On expiry (paid plan, past plan_ends_at) the subscription is marked
  expired and the account returns to the Free plan with the Free allowance.
  Call ``refresh_subscription_state`` on reads that depend on the plan.
"""
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..models import (
    User,
    Subscription,
    CreditLedger,
    Promotion,
    PaymentRequest,
    AuditLog,
)
from ..plans import (
    PLANS,
    PROMO_ENDS_AT,
    PROMO_MAX_SEATS,
    PROMO_DISCOUNT_PCT,
    PROMO_PRICE_PKR,
    subscription_end,
    promotion_is_active,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite stores datetimes naive; treat a naive value as UTC so
    comparisons with aware datetimes never raise TypeError."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def get_or_create_promotion(db) -> Promotion:
    promo = db.get(Promotion, 1)
    if promo is None:
        promo = Promotion(
            id=1,
            name="Pro launch promotion",
            discount_pct=PROMO_DISCOUNT_PCT,
            promo_price_pkr=PROMO_PRICE_PKR,
            normal_price_pkr=PLANS["pro"]["price_pkr"],
            max_seats=PROMO_MAX_SEATS,
            seats_reserved=0,
            seats_used=0,
            ends_at=PROMO_ENDS_AT,
            is_active=True,
        )
        db.add(promo)
        db.flush()
    return promo


def reserve_promo_seat(db) -> bool:
    """Atomically reserve one promotion seat. Returns True when a seat was
    reserved. The UPDATE ... WHERE is atomic on SQLite and PostgreSQL, so
    seats can never be oversold even under concurrent submits."""
    promo = get_or_create_promotion(db)
    if not promotion_is_active(promo):
        return False
    result = db.execute(
        update(Promotion)
        .where(Promotion.id == 1)
        .where(Promotion.seats_reserved + Promotion.seats_used < Promotion.max_seats)
        .values(seats_reserved=Promotion.seats_reserved + 1)
    )
    return result.rowcount == 1


def release_promo_seat(db) -> None:
    promo = get_or_create_promotion(db)
    if promo.seats_reserved > 0:
        promo.seats_reserved -= 1


def convert_promo_seat(db) -> None:
    promo = get_or_create_promotion(db)
    if promo.seats_reserved > 0:
        promo.seats_reserved -= 1
    promo.seats_used += 1


def classify_payment(user: User, plan: str, now: datetime | None = None) -> str:
    """Classify a payment for ``plan`` by this user.

    Returns one of:
    - "first_purchase": user has no active paid subscription for this plan.
    - "renewal": same plan, active subscription, not yet expired.
    - "plan_change": user holds an ACTIVE paid subscription for a DIFFERENT
      plan. Approval is refused until the owner explicitly changes the plan
      in Admin User Management (plan-change credit/expiry treatment is not
      defined by the product decisions and must be confirmed per case).
    """
    now = now or utcnow()
    if plan == "free":
        return "plan_change"
    has_active_paid = (
        user.plan in ("lite", "pro")
        and user.plan_ends_at is not None
        and _as_aware(user.plan_ends_at) > now
    )
    if not has_active_paid:
        return "first_purchase"
    if user.plan == plan:
        return "renewal"
    return "plan_change"


def refresh_subscription_state(db, user: User, now: datetime | None = None) -> bool:
    """If a paid subscription has expired, mark it expired and return the
    account to Free. Returns True when a change was made."""
    now = now or utcnow()
    if user.role == "owner" or user.complimentary_premium:
        return False
    ends_at = _as_aware(user.plan_ends_at)
    if user.plan in ("lite", "pro") and ends_at is not None and ends_at <= now:
        sub = db.scalar(
            select(Subscription)
            .where(Subscription.user_id == user.id, Subscription.status == "active")
            .order_by(Subscription.id.desc())
        )
        if sub is not None:
            sub.status = "expired"
        user.plan = "free"
        user.credits = PLANS["free"]["monthly_clips"]
        user.clips_used = 0
        user.plan_started_at = now
        user.plan_ends_at = None
        from ..activity import log_activity

        log_activity(db, user.id, "subscription_started", "Paid subscription expired; returned to the Free plan.")
        return True
    return False


def _grant_credits(db, user: User, amount: int, reason: str, ref_type: str = "", ref_id: int | None = None):
    user.credits += amount
    db.add(CreditLedger(user_id=user.id, delta=amount, reason=reason, ref_type=ref_type, ref_id=ref_id))


def apply_payment(db, payment: PaymentRequest, reviewer: User, now: datetime | None = None) -> Subscription:
    """Approve a pending payment and apply its subscription. Raises
    ValueError with a human-readable message when it cannot be applied.
    This function is idempotent-safe: it refuses any payment that is not
    pending, so double approval (double crediting) is impossible."""
    now = now or utcnow()
    if payment.status != "pending":
        raise ValueError(f"Payment #{payment.id} has already been {payment.status}.")
    user = db.get(User, payment.user_id)
    if user is None:
        raise ValueError("The paying user no longer exists.")
    if payment.plan not in ("lite", "pro"):
        raise ValueError("Only Lite and Pro can be purchased.")
    kind = classify_payment(user, payment.plan, now)
    if kind == "plan_change":
        raise ValueError(
            "Plan change: this customer holds an active subscription for a different plan. "
            "Plan-change credit/expiry treatment must be confirmed explicitly — change the plan "
            "in Admin User Management first (audited), then approve this payment."
        )
    plan = PLANS[payment.plan]
    # Renewal (same plan, before expiry): carry forward unused credits.
    # First purchase: fresh credit balance.
    if kind == "renewal":
        _grant_credits(
            db, user, plan["monthly_clips"],
            f"Renewal: {plan['name']} credits (unused credits carried forward)",
            ref_type="payment", ref_id=payment.id,
        )
    else:
        # First purchase: replace the balance with the new plan allowance.
        # (Free credits are replaced, not added — a new paid plan starts clean.)
        old = user.credits
        user.credits = plan["monthly_clips"]
        db.add(CreditLedger(user_id=user.id, delta=plan["monthly_clips"] - old,
                            reason=f"New subscription: {plan['name']} credits",
                            ref_type="payment", ref_id=payment.id))
    user.plan = payment.plan
    user.plan_started_at = now
    user.plan_ends_at = subscription_end(now)
    user.clips_used = 0
    sub = Subscription(
        user_id=user.id,
        plan=payment.plan,
        started_at=now,
        ends_at=subscription_end(now),
        credits_granted=plan["monthly_clips"],
        status="active",
        is_promotional=payment.is_promotional,
        promo_price_pkr=payment.amount_pkr if payment.is_promotional else None,
        payment_request_id=payment.id,
    )
    db.add(sub)
    db.flush()
    payment.status = "approved"
    payment.reviewer_id = reviewer.id
    payment.decided_at = now
    if payment.is_promotional:
        convert_promo_seat(db)
    db.add(AuditLog(
        actor_user_id=reviewer.id,
        action="payment_approved",
        detail=f"Payment #{payment.id} approved for {user.username}: {plan['name']} ({kind}), PKR {payment.amount_pkr}.",
    ))
    from ..activity import log_activity

    log_activity(db, user.id, "payment_approved", f"{plan['name']} subscription activated ({kind}).")
    if kind == "renewal":
        log_activity(db, user.id, "subscription_renewed", f"{plan['name']} renewed; unused credits carried forward.")
    else:
        log_activity(db, user.id, "subscription_started", f"{plan['name']} subscription started.")
    return sub


def reject_payment(db, payment: PaymentRequest, reviewer: User, note: str = "", now: datetime | None = None) -> None:
    if payment.status != "pending":
        raise ValueError(f"Payment #{payment.id} has already been {payment.status}.")
    now = now or utcnow()
    payment.status = "rejected"
    payment.reviewer_id = reviewer.id
    payment.review_note = note[:500]
    payment.decided_at = now
    if payment.is_promotional:
        release_promo_seat(db)
    db.add(AuditLog(
        actor_user_id=reviewer.id,
        action="payment_rejected",
        detail=f"Payment #{payment.id} rejected for user_id={payment.user_id}. Note: {note[:200]}",
    ))
    from ..activity import log_activity

    log_activity(db, payment.user_id, "payment_rejected", "Your payment was not verified. Please check the transaction details and resubmit.")
