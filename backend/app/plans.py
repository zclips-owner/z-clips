"""Plan definitions, entitlements, promotion and subscription logic.

Prices, clip allowances and feature flags are the exact decisions from the
master prompt. Do not change them here; change the master prompt first.

Promotion (Pro launch): normal PKR 7,000, 25% off = PKR 5,250, max 20 seats,
ends 30 Nov 2026 or when seats are consumed. The promo price applies to the
FIRST 30-day promotional Pro subscription only; renewal is at the normal
price unless a new offer is explicitly configured.

Credit carry-forward: renewing the SAME plan before expiry keeps unused
credits and adds the new plan credits; validity restarts for 30 days.
Implemented transactionally (see app/services/billing.py). Plan-change
behaviour is deliberately NOT implemented here — it must be confirmed by
the owner before implementation (see billing.apply_payment).
"""
from datetime import datetime, timedelta, timezone

# NOTE on `advanced_positioning`: it gates 4K export and YouTube live
# monitoring ONLY. Manual facecam positioning and every editor feature
# (aspect ratio, layout, caption typography, sticker, social-post card)
# are gated on `manual_editing` instead, so Lite users get them too.
PLANS = {
    "free": {
        "name": "Free",
        "price_pkr": 0,
        "monthly_clips": 5,
        "manual_editing": False,
        "caption_styles": 1,
        "youtube_live_monitoring": False,
        "4k_export": False,
        "advanced_positioning": False,
    },
    "lite": {
        "name": "Monthly Lite",
        "price_pkr": 1500,
        "monthly_clips": 20,
        "manual_editing": True,
        "caption_styles": 3,
        "youtube_live_monitoring": False,
        "4k_export": False,
        "advanced_positioning": False,
    },
    "pro": {
        "name": "Monthly Pro",
        "price_pkr": 7000,
        "monthly_clips": 70,
        "manual_editing": True,
        "caption_styles": "all",
        "youtube_live_monitoring": True,
        "4k_export": True,
        "advanced_positioning": True,
    },
}

# Caption styles available per plan. "simple" is the one Free style.
CAPTION_STYLES = {
    "simple": {"name": "Simple", "description": "Clean white text, bottom centre."},
    "bold": {"name": "Bold Pop", "description": "Bold words with a pop highlight."},
    "karaoke": {"name": "Karaoke", "description": "Word-by-word highlight as it is spoken."},
    "minimal": {"name": "Minimal", "description": "Small, understated captions."},
    "neon": {"name": "Neon", "description": "Bright neon outline, high contrast."},
}
FREE_CAPTION_STYLES = ["simple"]
LITE_CAPTION_STYLES = ["simple", "bold", "karaoke"]
PRO_CAPTION_STYLES = list(CAPTION_STYLES.keys())

# Editor options (YIZERGE-style editor features, unlocked for Lite + Pro
# via the `manual_editing` entitlement — see app/editor_fields.py).
# `advanced_positioning` is now ONLY about 4K export and YouTube live
# monitoring; it no longer gates manual facecam positioning or any editor
# feature. Free keeps manual_editing=False, so Free users get none of
# these and no manual facecam.
ASPECT_RATIOS = ["9:16", "1:1", "4:3", "16:9"]
LAYOUTS = ["standard", "facecam_top", "facecam_side"]
CAPTION_SIZES = ["small", "medium", "large"]
CAPTION_POSITIONS = ["bottom", "middle", "top"]
CAPTION_BGS = ["none", "box", "outline"]

# (width, height) ratio numerators for each aspect ratio. The export height
# is the height; the width is derived from it (e.g. 16:9 at 1280 -> 2276x1280).
ASPECT_RATIO_DIMS = {
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:3": (4, 3),
    "16:9": (16, 9),
}
DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_LAYOUT = "standard"
DEFAULT_CAPTION_SIZE = "medium"
DEFAULT_CAPTION_POSITION = "bottom"
DEFAULT_CAPTION_BG = "outline"
MAX_STICKER_CHARS = 40
MAX_POST_AUTHOR_CHARS = 40
MAX_POST_TEXT_CHARS = 140

PROMO_ENDS_AT = datetime(2026, 11, 30, 23, 59, 59, tzinfo=timezone.utc)
PROMO_MAX_SEATS = 20
PROMO_DISCOUNT_PCT = 25
PROMO_PRICE_PKR = 5250  # 7000 * 0.75
SUBSCRIPTION_DAYS = 30


def entitlements_for(user):
    """Server-side entitlements. Owner and complimentary-premium users get
    lifetime Pro-equivalent access with no payment requirement."""
    if user.role == "owner" or getattr(user, "complimentary_premium", False):
        return {
            "name": "Owner Lifetime Premium" if user.role == "owner" else "Complimentary Premium",
            "monthly_clips": None,
            "manual_editing": True,
            "caption_styles": "all",
            "caption_style_list": PRO_CAPTION_STYLES,
            "youtube_live_monitoring": True,
            "4k_export": True,
            "advanced_positioning": True,
            "payment_required": False,
        }
    plan = PLANS.get(user.plan, PLANS["free"]).copy()
    plan["payment_required"] = user.plan != "free"
    if user.plan == "free":
        plan["caption_style_list"] = FREE_CAPTION_STYLES
    elif user.plan == "lite":
        plan["caption_style_list"] = LITE_CAPTION_STYLES
    else:
        plan["caption_style_list"] = PRO_CAPTION_STYLES
    return plan


def caption_style_allowed(user, style: str) -> bool:
    ent = entitlements_for(user)
    return style in ent["caption_style_list"]


def export_heights_allowed(user):
    """Render heights the user's plan may request (720p default)."""
    ent = entitlements_for(user)
    if ent["4k_export"]:
        return [1280, 1920, 3840]
    return [1280]


def promotion_is_active(promo, now: datetime | None = None) -> bool:
    """True while the promotion is enabled, before its end date, and while
    seats remain."""
    if promo is None:
        return False
    now = now or datetime.now(timezone.utc)
    ends_at = promo.ends_at
    if ends_at is not None and ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    if ends_at is not None and now > ends_at:
        return False
    if not promo.is_active:
        return False
    return promo.seats_remaining > 0


def promotion_price_for(plan_key: str, promo, now: datetime | None = None):
    """Return the promo price for a plan, or None when no promotion applies.
    Only Pro has a promotion."""
    if plan_key != "pro":
        return None
    if not promotion_is_active(promo, now):
        return None
    return promo.promo_price_pkr


def subscription_end(start: datetime) -> datetime:
    return start + timedelta(days=SUBSCRIPTION_DAYS)
