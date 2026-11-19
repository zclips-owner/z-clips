"""Unit tests for the billing service: credit carry-forward, promotion
seat locking, payment approval idempotency, and plan-change refusal.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import User, PaymentRequest
from app.services import billing
from app.plans import PLANS


def _user(db, username="u1", plan="free", credits=5, ends_at=None):
    user = User(username=username, email=f"{username}@example.com",
                password_hash="x", role="customer", plan=plan,
                credits=credits, plan_ends_at=ends_at)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _payment(db, user, plan="lite", amount=1500, promo=False):
    p = PaymentRequest(user_id=user.id, plan=plan, amount_pkr=amount,
                       is_promotional=promo, txn_id="TXN-1",
                       screenshot_path="/tmp/x.png", status="pending")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _owner(db):
    owner = User(username="zclips_owner", email="o@x.local", password_hash="x",
                 role="owner", plan="pro")
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return owner


@pytest.fixture()
def db(tmp_db):
    session = tmp_db()
    try:
        yield session
    finally:
        session.close()


def test_first_purchase_sets_plan_and_credits(db):
    user = _user(db, plan="free", credits=5)
    owner = _owner(db)
    payment = _payment(db, user, plan="lite", amount=1500)
    sub = billing.apply_payment(db, payment, owner)
    db.commit()
    assert payment.status == "approved"
    assert user.plan == "lite"
    assert user.credits == 20
    assert sub.plan == "lite"
    assert sub.credits_granted == 20
    assert (sub.ends_at - sub.started_at).days == 30


def test_renewal_carries_unused_credits_forward(db):
    now = datetime.now(timezone.utc)
    user = _user(db, plan="lite", credits=7,
                 ends_at=now + timedelta(days=5))  # 7 unused, still active
    owner = _owner(db)
    payment = _payment(db, user, plan="lite", amount=1500)
    billing.apply_payment(db, payment, owner)
    db.commit()
    # 7 unused carried forward + 20 new = 27
    assert user.credits == 27
    assert user.plan == "lite"
    ends_at = user.plan_ends_at
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    assert ends_at > now + timedelta(days=29)


def test_double_approval_is_refused(db):
    user = _user(db, plan="free", credits=5)
    owner = _owner(db)
    payment = _payment(db, user, plan="lite", amount=1500)
    billing.apply_payment(db, payment, owner)
    db.commit()
    with pytest.raises(ValueError, match="already been approved"):
        billing.apply_payment(db, payment, owner)
    # credits were only granted once
    assert user.credits == 20


def test_plan_change_approval_is_refused(db):
    now = datetime.now(timezone.utc)
    user = _user(db, plan="lite", credits=10,
                 ends_at=now + timedelta(days=10))
    owner = _owner(db)
    payment = _payment(db, user, plan="pro", amount=7000)
    with pytest.raises(ValueError, match="Plan change"):
        billing.apply_payment(db, payment, owner)
    assert payment.status == "pending"
    assert user.plan == "lite"
    assert user.credits == 10


def test_reject_releases_promo_seat(db):
    user = _user(db, plan="free", credits=5)
    owner = _owner(db)
    assert billing.reserve_promo_seat(db) is True
    promo = billing.get_or_create_promotion(db)
    assert promo.seats_reserved == 1
    payment = _payment(db, user, plan="pro", amount=5250, promo=True)
    billing.reject_payment(db, payment, owner, note="not received")
    db.commit()
    assert payment.status == "rejected"
    assert promo.seats_reserved == 0


def test_promo_seats_cannot_be_oversold(db):
    promo = billing.get_or_create_promotion(db)
    promo.max_seats = 2
    db.commit()
    assert billing.reserve_promo_seat(db) is True
    assert billing.reserve_promo_seat(db) is True
    assert billing.reserve_promo_seat(db) is False
    assert promo.seats_reserved == 2


def test_approve_converts_reserved_seat_to_used(db):
    user = _user(db, plan="free", credits=5)
    owner = _owner(db)
    assert billing.reserve_promo_seat(db) is True
    payment = _payment(db, user, plan="pro", amount=5250, promo=True)
    billing.apply_payment(db, payment, owner)
    db.commit()
    promo = billing.get_or_create_promotion(db)
    assert promo.seats_reserved == 0
    assert promo.seats_used == 1
    assert user.plan == "pro"
    assert user.credits == 70


def test_expired_subscription_reverts_to_free(db):
    now = datetime.now(timezone.utc)
    user = _user(db, plan="lite", credits=3,
                 ends_at=now - timedelta(days=1))
    changed = billing.refresh_subscription_state(db, user, now)
    db.commit()
    assert changed is True
    assert user.plan == "free"
    assert user.credits == PLANS["free"]["monthly_clips"]


def test_active_subscription_is_not_reverted(db):
    now = datetime.now(timezone.utc)
    user = _user(db, plan="lite", credits=3,
                 ends_at=now + timedelta(days=5))
    changed = billing.refresh_subscription_state(db, user, now)
    assert changed is False
    assert user.plan == "lite"
