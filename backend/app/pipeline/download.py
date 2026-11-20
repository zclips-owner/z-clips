"""Download a source video from a URL with yt-dlp.

URL-source clip jobs are created with an empty ``stored_path`` — nothing
has the video on disk yet. The pipeline must fetch it before analysis
can probe it, and this module is the only place that downloads a URL.

Honesty rules enforced here:
- A failed download raises :class:`DownloadError` with a short,
  human-readable message. The caller records it on the job and fails
  the job; no clip is ever invented from a failed download.
- TLS verification is NEVER disabled. yt-dlp ships its own certifi
  bundle, which ignores a CA bundle the environment configured (e.g.
  ``SSL_CERT_FILE`` for a sandbox egress proxy); when one is
  configured, yt-dlp is told to use the system certificate store
  instead, so verification still happens — against the configured
  bundle.
- Downloads are capped: best video at most 1080p + best audio, merged
  to mp4, and the finished file must fit inside ``MAX_UPLOAD_MB`` (the
  same cap as an uploaded file) so the small free-tier container is not
  filled by one huge source.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from ..config import MAX_UPLOAD_MB

MAX_DOWNLOAD_MB = MAX_UPLOAD_MB  # same cap as an uploaded file


class DownloadError(RuntimeError):
    """The source URL could not be downloaded (bad URL, host blocking it, ...)."""


def _ydl_options(out_dir: Path) -> dict:
    opts: dict = {
        "format": (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080][ext=mp4]/best[height<=1080]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "noprogress": True,  # keep the worker's stdout log clean
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        # YouTube sometimes challenges non-browser clients; the Android
        # player client is the usual way a plain download still works.
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    # yt-dlp's bundled certifi store ignores a CA bundle the environment
    # configured (SSL_CERT_FILE / SSL_CERT_DIR). When one is configured,
    # use the system store instead so that bundle is honoured.
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        opts["compat_opts"] = ["no-certifi"]
    return opts


def _short_error(message: str) -> str:
    """One short, human-readable line out of yt-dlp's often long errors."""
    text = (message or "").strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = text.split("please report this issue")[0].strip()
    text = " ".join(text.split())
    return text[:400] or "download failed"


def download_source_url(url: str, dest_dir: Path) -> Path:
    """Download *url* into a fresh sub-directory of *dest_dir*.

    Returns the downloaded file path (mp4). Raises :class:`DownloadError`
    with a clear message on any failure. The caller owns the returned
    file (and the sub-directory it sits in).
    """
    try:
        import yt_dlp  # noqa: PLC0415  (only the download path needs it)
    except ImportError as exc:
        raise DownloadError(
            "Could not download video from this URL — the video downloader "
            "(yt-dlp) is not installed on the server."
        ) from exc

    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise DownloadError(
            "Could not download video from this URL — the URL is not a valid http(s) address."
        )
    out_dir = Path(dest_dir) / f"dl_{uuid.uuid4().hex}"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        try:
            with yt_dlp.YoutubeDL(_ydl_options(out_dir)) as ydl:
                ydl.download([url])
        except Exception as exc:
            raise DownloadError(
                f"Could not download video from this URL — {_short_error(str(exc))}"
            ) from exc
        files = [
            p for p in out_dir.iterdir()
            if p.is_file() and p.suffix.lower() not in (".part", ".ytdl")
        ]
        if not files:
            raise DownloadError(
                "Could not download video from this URL — the download produced no video file."
            )
        # yt-dlp may leave sidecar files (e.g. .description); take the video.
        video = max(files, key=lambda p: p.stat().st_size)
        size = video.stat().st_size
        if size <= 0:
            raise DownloadError(
                "Could not download video from this URL — the downloaded file is empty."
            )
        if size > MAX_DOWNLOAD_MB * 1024 * 1024:
            raise DownloadError(
                f"Could not download video from this URL — the video is larger "
                f"than the {MAX_DOWNLOAD_MB} MB limit."
            )
        return video
    except Exception:
        # A failed/oversized download leaves nothing behind to confuse a retry.
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
