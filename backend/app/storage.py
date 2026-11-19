"""File storage helpers: per-user directories, extension/size validation
and storage statistics for the owner monitoring page.
"""
import os
import shutil
from pathlib import Path

from .config import (
    UPLOAD_DIR,
    CLIP_DIR,
    PAYMENT_DIR,
    MAX_UPLOAD_MB,
    MAX_SCREENSHOT_MB,
    ALLOWED_VIDEO_EXTS,
    ALLOWED_IMAGE_EXTS,
)


def user_upload_dir(user_id: int) -> Path:
    d = UPLOAD_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_clip_dir(user_id: int) -> Path:
    d = CLIP_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_payment_dir(user_id: int) -> Path:
    d = PAYMENT_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_allowed_video(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_VIDEO_EXTS


def is_allowed_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_IMAGE_EXTS


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    keep = "".join(c for c in name if c.isalnum() or c in ("-", "_", "."))
    return keep[:120] or "file"


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total


def storage_stats() -> dict:
    from .config import STORAGE_DIR

    return {
        "storage_dir": str(STORAGE_DIR),
        "uploads_bytes": dir_size_bytes(UPLOAD_DIR),
        "clips_bytes": dir_size_bytes(CLIP_DIR),
        "payments_bytes": dir_size_bytes(PAYMENT_DIR),
        "total_bytes": dir_size_bytes(STORAGE_DIR),
        "disk_free_bytes": shutil.disk_usage(STORAGE_DIR).free,
        "disk_total_bytes": shutil.disk_usage(STORAGE_DIR).total,
    }
