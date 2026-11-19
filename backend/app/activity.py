"""User-visible activity feed. Every entry is created server-side when a
real event happens (clip created/approved/downloaded, payment submitted /
decided, subscription renewed, plan changed, support reply, live status).
"""
from .models import Activity


def log_activity(db, user_id: int, kind: str, detail: str = "") -> Activity:
    entry = Activity(user_id=user_id, kind=kind, detail=detail[:500])
    db.add(entry)
    return entry


ACTIVITY_LABELS = {
    "clip_created": "Clip job created",
    "clip_ready": "Clip is ready",
    "clip_approved": "Clip approved",
    "clip_rejected": "Clip rejected",
    "clip_downloaded": "Clip downloaded",
    "payment_submitted": "Payment submitted",
    "payment_approved": "Payment approved",
    "payment_rejected": "Payment rejected",
    "subscription_started": "Subscription started",
    "subscription_renewed": "Subscription renewed",
    "plan_changed": "Plan changed",
    "credits_adjusted": "Credits adjusted",
    "support_opened": "Support request opened",
    "support_replied": "Support reply sent",
    "yt_channel_added": "YouTube channel added",
    "yt_live": "YouTube channel went live",
    "account_created": "Account created",
}
