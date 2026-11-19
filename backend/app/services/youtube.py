"""YouTube live-status detection (public status only) and authorised-source
handling.

Rules (master prompt):
- Public live-status detection is SEPARATE from authorised video ingestion.
- Never download or process arbitrary public streams. Ingestion only ever
  happens from an authorised source the user provides.
- Without a YouTube Data API key the service returns a truthful
  "not_configured" state — it never pretends to know a live status.
"""
import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from ..config import YOUTUBE_API_KEY

API_BASE = "https://www.googleapis.com/youtube/v3"


def parse_channel_input(value: str) -> dict:
    """Parse a YouTube channel URL or @handle into {channel_url, handle,
    channel_id}. Returns {} when the input is not recognisable."""
    value = (value or "").strip()
    if not value:
        return {}
    # https://www.youtube.com/@handle
    m = re.search(r"youtube\.com/@([A-Za-z0-9_.\-]+)", value)
    if m:
        return {"channel_url": value, "handle": "@" + m.group(1), "channel_id": ""}
    # https://www.youtube.com/channel/UCxxxx
    m = re.search(r"youtube\.com/channel/([A-Za-z0-9_\-]+)", value)
    if m:
        return {"channel_url": value, "handle": "", "channel_id": m.group(1)}
    # https://www.youtube.com/c/name or /user/name
    m = re.search(r"youtube\.com/(?:c|user)/([A-Za-z0-9_.\-]+)", value)
    if m:
        return {"channel_url": value, "handle": m.group(1), "channel_id": ""}
    # bare @handle
    if re.fullmatch(r"@[A-Za-z0-9_.\-]+", value):
        return {"channel_url": f"https://www.youtube.com/{value}", "handle": value, "channel_id": ""}
    return {}


def _api_get(path: str, params: dict, timeout: int = 15) -> dict:
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not configured.")
    params = dict(params)
    params["key"] = YOUTUBE_API_KEY
    url = API_BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Z-CLIPS/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_channel_id(channel) -> str:
    """Resolve a handle to a channel id via the API. Returns '' when it
    cannot be resolved (or when no API key is configured)."""
    if getattr(channel, "channel_id", ""):
        return channel.channel_id
    if not YOUTUBE_API_KEY:
        return ""
    handle = (getattr(channel, "channel_handle", "") or "").lstrip("@")
    if not handle:
        return ""
    try:
        data = _api_get("/channels", {"part": "id", "forHandle": "@" + handle})
        items = data.get("items", [])
        return items[0]["id"] if items else ""
    except Exception:
        return ""


def check_live_status(channel) -> dict:
    """Check whether a channel is live right now.

    Returns a dict: {
      "configured": bool,        # False when no API key is set
      "status": str,             # live|offline|not_checked|not_configured|error
      "is_live": bool|None,
      "live_video_id": str,
      "live_title": str,
      "error": str,
    }
    This only READS public live status. It never downloads video.
    """
    if not YOUTUBE_API_KEY:
        return {
            "configured": False,
            "status": "not_configured",
            "is_live": None,
            "live_video_id": "",
            "live_title": "",
            "error": "YouTube Data API key is not configured. Set YOUTUBE_API_KEY to enable live-status checks.",
        }
    channel_id = resolve_channel_id(channel)
    if not channel_id:
        return {
            "configured": True,
            "status": "error",
            "is_live": None,
            "live_video_id": "",
            "live_title": "",
            "error": "Could not resolve this channel. Check the URL/handle and try again.",
        }
    try:
        # search for live broadcasts on this channel
        data = _api_get("/search", {
            "part": "snippet",
            "channelId": channel_id,
            "eventType": "live",
            "type": "video",
            "maxResults": 1,
        })
        items = data.get("items", [])
        if items:
            item = items[0]
            return {
                "configured": True,
                "status": "live",
                "is_live": True,
                "live_video_id": item["id"].get("videoId", ""),
                "live_title": item["snippet"].get("title", ""),
                "error": "",
            }
        return {
            "configured": True,
            "status": "offline",
            "is_live": False,
            "live_video_id": "",
            "live_title": "",
            "error": "",
        }
    except Exception as exc:  # network/API error — report honestly
        return {
            "configured": True,
            "status": "error",
            "is_live": None,
            "live_video_id": "",
            "live_title": "",
            "error": f"YouTube API check failed: {exc}",
        }
