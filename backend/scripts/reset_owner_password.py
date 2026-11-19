"""Controlled owner-password recovery for Z CLIPS.

There is no email-based password reset (no email provider is configured).
If the owner password is lost, an operator with shell access runs:

    .venv/bin/python scripts/reset_owner_password.py

The script prompts for a new password (minimum 16 characters, typed
twice, not echoed), updates the owner row, and writes an AuditLog entry.
It never prints the password and never accepts it as a CLI argument
(so it does not end up in shell history).

This is deliberately NOT exposed as a web endpoint.
"""
import sys
import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from app.database import SessionLocal
from app.models import User, AuditLog
from app.security import hash_password


def main() -> int:
    db = SessionLocal()
    try:
        owner = db.scalar(select(User).where(User.role == "owner"))
        if owner is None:
            print("No owner account exists. Use /owner-setup instead.")
            return 1
        pw = getpass.getpass("New owner password (min 16 chars): ")
        pw2 = getpass.getpass("Confirm new owner password: ")
        if len(pw) < 16:
            print("Password must be at least 16 characters.")
            return 1
        if pw != pw2:
            print("Passwords do not match.")
            return 1
        owner.password_hash = hash_password(pw)
        db.add(AuditLog(
            actor_user_id=owner.id,
            action="owner_password_reset",
            detail="Owner password reset via controlled shell recovery.",
        ))
        db.commit()
        print("Owner password updated and audited.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
