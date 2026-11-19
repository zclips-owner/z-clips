"""Seed / bootstrap helper for Z CLIPS.

Creates the singleton Pro promotion row (if missing) and prints the
current configuration state. Safe to run repeatedly (idempotent).

Usage:
    .venv/bin/python scripts/seed.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, engine, SessionLocal
from app.services.billing import get_or_create_promotion
from app.config import NAYAPAY_QR_PATH, YOUTUBE_API_KEY
import os
import shutil


def main() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        promo = get_or_create_promotion(db)
        db.commit()
        print(f"Promotion: {promo.name}")
        print(f"  price: PKR {promo.normal_price_pkr} -> promo PKR {promo.promo_price_pkr} ({promo.discount_pct}% off)")
        print(f"  seats: {promo.seats_used} used / {promo.seats_reserved} reserved / {promo.max_seats} max")
        print(f"  ends:  {promo.ends_at}  active={promo.is_active}")
    finally:
        db.close()
    print(f"NayaPay QR: {'provided' if os.path.exists(NAYAPAY_QR_PATH) else 'NOT PROVIDED (payment page shows the honest state)'} -> {NAYAPAY_QR_PATH}")
    print(f"YouTube API key: {'configured' if YOUTUBE_API_KEY else 'NOT configured (live-status checks report not_configured)'}")
    print(f"ffmpeg: {shutil.which('ffmpeg') or 'NOT FOUND on PATH (clip rendering will fail)'}")
    print("Seed complete. Create the owner at /owner-setup with OWNER_SETUP_TOKEN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
