import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "development")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./zclips.db")
SESSION_SECRET = os.getenv("SESSION_SECRET", "unsafe-development-secret-change-me")
OWNER_SETUP_TOKEN = os.getenv("OWNER_SETUP_TOKEN", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# --- storage ----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", str(BASE_DIR / "storage")))
UPLOAD_DIR = STORAGE_DIR / "uploads"
CLIP_DIR = STORAGE_DIR / "clips"
PAYMENT_DIR = STORAGE_DIR / "payments"
MODEL_DIR = Path(os.getenv("MODEL_DIR", str(STORAGE_DIR / "models")))
for _d in (UPLOAD_DIR, CLIP_DIR, PAYMENT_DIR, MODEL_DIR):
    _d.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2048"))
MAX_SCREENSHOT_MB = int(os.getenv("MAX_SCREENSHOT_MB", "10"))
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# --- NayaPay -----------------------------------------------------------------
# The QR code image is supplied by the owner (place it at this path).
# When no file exists there, the payment page shows an explicit
# "QR code not yet provided" configuration state — never a fake QR.
NAYAPAY_QR_PATH = os.getenv("NAYAPAY_QR_PATH", str(STORAGE_DIR / "nayapay-qr.png"))
NAYAPAY_ACCOUNT_HOLDER = "Zain ul abideen"
NAYAPAY_NUMBER = "03296195965"
NAYAPAY_QR_INSTRUCTION = (
    "If scanning the QR code does not show the account holder name "
    "Zain ul abideen, use the NayaPay number 03296195965 instead."
)

# --- YouTube -----------------------------------------------------------------
# YouTube Data API v3 key for public live-status checks (optional).
# Without it, YT Monitoring shows a clear "not configured" state.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# --- worker ------------------------------------------------------------------
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "5"))
WORKER_ID = os.getenv("WORKER_ID", "worker-1")

# Trust X-Forwarded-For only when behind a trusted reverse proxy.
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"

if APP_ENV == "production":
    if SESSION_SECRET == "unsafe-development-secret-change-me" or len(SESSION_SECRET) < 32:
        raise RuntimeError("Set a strong SESSION_SECRET (at least 32 characters) in production.")
    if not OWNER_SETUP_TOKEN or len(OWNER_SETUP_TOKEN) < 32:
        raise RuntimeError("Set a strong OWNER_SETUP_TOKEN in production.")
