"""YouTube routes: Pro live-monitoring channel list.

Public live-status detection only — this never downloads video. Ingestion
only ever happens from an authorised source the user provides (recorded
via the authorize route). Without a YouTube Data API key the service
truthfully reports "not_configured" and no live status is invented.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..activity import log_activity
from ..config import YOUTUBE_API_KEY
from ..database import get_db
from ..deps import base_context, check_rate_limit, require_pro, require_user, validate_csrf
from ..models import YTChannel, utcnow
from ..plans import entitlements_for
from ..render import render
from ..services.youtube import check_live_status, parse_channel_input

router = APIRouter()


def _render_yt(request: Request, db: Session, user, error: str | None = None, status_code: int = 200):
    channels = db.scalars(
        select(YTChannel)
        .where(YTChannel.user_id == user.id)
        .order_by(YTChannel.id.desc())
    ).all()
    entitlements = entitlements_for(user)
    return render(
        request,
        "yt.html",
        {
            **base_context(request, user),
            "channels": channels,
            "api_configured": bool(YOUTUBE_API_KEY),
            "can_monitor": bool(entitlements["youtube_live_monitoring"]),
            "error": error,
        },
        status_code=status_code,
    )


def _own_channel(db: Session, channel_id: int, user) -> YTChannel:
    """Return the channel or raise 404 / 403 (Pro users: own channels only)."""
    channel = db.get(YTChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="YouTube channel not found.")
    if channel.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only manage your own channels.")
    return channel


@router.get("/yt")
def yt_page(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    # Free/Lite see the locked state in-page; the page itself is not 403.
    return _render_yt(request, db, user)


@router.post("/yt/channels")
def add_channel(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_pro),
    channel_url: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))

    parsed = parse_channel_input(channel_url)
    if not parsed:
        return _render_yt(
            request, db, user,
            error="Enter a valid YouTube channel URL or @handle.",
            status_code=400,
        )
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
    return RedirectResponse("/yt", status_code=303)


@router.post("/yt/channels/{channel_id}/check")
def check_channel(
    request: Request,
    channel_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_pro),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))

    channel = _own_channel(db, channel_id, user)
    result = check_live_status(channel)
    was_live = channel.is_live
    prev_live_video_id = channel.live_video_id or ""
    channel.last_checked_at = utcnow()
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
    if not result["configured"]:
        # No API key: do not invent a status; show the honest message.
        return _render_yt(request, db, user, error=result["error"])
    if result["status"] == "error":
        return _render_yt(request, db, user, error=result["error"])
    return RedirectResponse("/yt", status_code=303)


@router.post("/yt/channels/{channel_id}/delete")
def delete_channel(
    request: Request,
    channel_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_pro),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))

    channel = _own_channel(db, channel_id, user)
    db.delete(channel)
    db.commit()
    return RedirectResponse("/yt", status_code=303)


@router.post("/yt/channels/{channel_id}/authorize")
def authorize_channel(
    request: Request,
    channel_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_pro),
    note: str = Form(""),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))

    channel = _own_channel(db, channel_id, user)
    note = (note or "").strip()
    if len(note) > 500:
        return _render_yt(
            request, db, user,
            error="Authorisation note must be 500 characters or fewer.",
            status_code=400,
        )
    # Records authorisation only — this never downloads anything.
    channel.ingestion_state = "authorized"
    channel.authorized_source_note = note
    db.commit()
    return RedirectResponse("/yt", status_code=303)
