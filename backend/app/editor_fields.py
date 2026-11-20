"""Shared validation for the clip editor fields (YIZERGE-style editor).

Used by BOTH the HTML router (app/routers/customer.py, Form data) and the
JSON API router (app/routers/api.py, JSON body) so the two surfaces accept
exactly the same values and enforce exactly the same entitlements.

Entitlement rule (user instruction): ALL editor features — aspect ratio,
framing layout, caption typography, sticker/callout, social-post card, and
manual facecam positioning — unlock for Lite AND Pro via `manual_editing`.
Free (manual_editing=False) gets none of them. `advanced_positioning` is
NOT used here; it only gates 4K export and YouTube live monitoring.
"""
from __future__ import annotations

from .plans import (
    ASPECT_RATIOS,
    CAPTION_BGS,
    CAPTION_POSITIONS,
    CAPTION_SIZES,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_CAPTION_BG,
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_SIZE,
    DEFAULT_LAYOUT,
    LAYOUTS,
    MAX_POST_AUTHOR_CHARS,
    MAX_POST_TEXT_CHARS,
    MAX_STICKER_CHARS,
)

EDITOR_OPTION_LISTS = {
    "aspect_ratios": list(ASPECT_RATIOS),
    "layouts": list(LAYOUTS),
    "caption_sizes": list(CAPTION_SIZES),
    "caption_positions": list(CAPTION_POSITIONS),
    "caption_bgs": list(CAPTION_BGS),
}

# Defaults for every new editor field (also the ClipJob column defaults).
EDITOR_DEFAULTS = {
    "aspect_ratio": DEFAULT_ASPECT_RATIO,
    "layout": DEFAULT_LAYOUT,
    "caption_size": DEFAULT_CAPTION_SIZE,
    "caption_position": DEFAULT_CAPTION_POSITION,
    "caption_bg": DEFAULT_CAPTION_BG,
    "sticker_text": "",
    "post_author": "",
    "post_text": "",
}


def _clean_str(raw, default: str = "") -> str:
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip()
    return str(raw).strip()


def parse_editor_fields(raw: dict, manual_editing: bool) -> tuple[dict, str | None]:
    """Validate raw editor field values.

    `raw` maps field name -> submitted value (str, number or None).
    Returns (cleaned_fields, error). `cleaned_fields` carries every field
    with defaults applied; `error` is a human-readable message or None.

    Any non-default editor value (or a manual facecam request, checked by
    the caller via facecam_mode) requires `manual_editing`; without it the
    whole set is rejected so a Free user cannot smuggle editor features
    past the form.
    """
    aspect_ratio = _clean_str(raw.get("aspect_ratio"), DEFAULT_ASPECT_RATIO)
    if aspect_ratio not in ASPECT_RATIOS:
        return {}, "Please choose a valid aspect ratio."
    layout = _clean_str(raw.get("layout"), DEFAULT_LAYOUT)
    if layout not in LAYOUTS:
        return {}, "Please choose a valid framing layout."
    caption_size = _clean_str(raw.get("caption_size"), DEFAULT_CAPTION_SIZE)
    if caption_size not in CAPTION_SIZES:
        return {}, "Please choose a valid caption size."
    caption_position = _clean_str(raw.get("caption_position"), DEFAULT_CAPTION_POSITION)
    if caption_position not in CAPTION_POSITIONS:
        return {}, "Please choose a valid caption position."
    caption_bg = _clean_str(raw.get("caption_bg"), DEFAULT_CAPTION_BG)
    if caption_bg not in CAPTION_BGS:
        return {}, "Please choose a valid caption background."
    sticker_text = _clean_str(raw.get("sticker_text"))
    if len(sticker_text) > MAX_STICKER_CHARS:
        return {}, f"Sticker text must be {MAX_STICKER_CHARS} characters or fewer."
    post_author = _clean_str(raw.get("post_author"))
    if len(post_author) > MAX_POST_AUTHOR_CHARS:
        return {}, f"Post author must be {MAX_POST_AUTHOR_CHARS} characters or fewer."
    post_text = _clean_str(raw.get("post_text"))
    if len(post_text) > MAX_POST_TEXT_CHARS:
        return {}, f"Post text must be {MAX_POST_TEXT_CHARS} characters or fewer."

    fields = {
        "aspect_ratio": aspect_ratio,
        "layout": layout,
        "caption_size": caption_size,
        "caption_position": caption_position,
        "caption_bg": caption_bg,
        "sticker_text": sticker_text,
        "post_author": post_author,
        "post_text": post_text,
    }
    # Anything beyond the plain-AI defaults is manual editing.
    uses_editor_features = (
        aspect_ratio != DEFAULT_ASPECT_RATIO
        or layout != DEFAULT_LAYOUT
        or caption_size != DEFAULT_CAPTION_SIZE
        or caption_position != DEFAULT_CAPTION_POSITION
        or caption_bg != DEFAULT_CAPTION_BG
        or bool(sticker_text)
        or bool(post_author)
        or bool(post_text)
    )
    if uses_editor_features and not manual_editing:
        return {}, "These editing options require the Lite or Pro plan."
    return fields, None


def editor_fields_for_job(job) -> dict:
    """Read the editor fields off a ClipJob, applying defaults for rows
    created before the columns existed."""
    return {
        "aspect_ratio": getattr(job, "aspect_ratio", None) or DEFAULT_ASPECT_RATIO,
        "layout": getattr(job, "layout", None) or DEFAULT_LAYOUT,
        "caption_size": getattr(job, "caption_size", None) or DEFAULT_CAPTION_SIZE,
        "caption_position": getattr(job, "caption_position", None) or DEFAULT_CAPTION_POSITION,
        "caption_bg": getattr(job, "caption_bg", None) or DEFAULT_CAPTION_BG,
        "sticker_text": getattr(job, "sticker_text", None) or "",
        "post_author": getattr(job, "post_author", None) or "",
        "post_text": getattr(job, "post_text", None) or "",
    }
