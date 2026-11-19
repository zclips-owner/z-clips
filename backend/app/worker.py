"""Background worker for Z CLIPS.

Run as:  python -m app.worker

Polls clip_jobs for status='queued' every WORKER_POLL_SECONDS, claims
ONE job atomically (UPDATE ... WHERE status='queued', rowcount must be
1, so two workers can never double-claim the same job), then calls
pipeline.run.process_job. Logs to stdout. Handles SIGTERM gracefully.

Also runs a periodic pass (default every 3600s) that calls
billing.refresh_subscription_state for users whose paid plan has
expired, so expired accounts return to Free even if nobody visits a
page that would otherwise trigger the refresh.
"""
from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import select, update

from .config import WORKER_ID, WORKER_POLL_SECONDS
from .database import SessionLocal
from .models import ClipJob, User
from .pipeline.run import process_job

BILLING_PASS_INTERVAL_SECONDS = float(os.getenv("BILLING_PASS_INTERVAL_SECONDS", "3600"))

_stop = False


def _log(msg: str) -> None:
    print(f"[worker {WORKER_ID} {datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _stop
    _log(f"Received signal {signum}; finishing the current job, then stopping.")
    _stop = True


def claim_one_job() -> int | None:
    """Atomically claim the oldest queued job. Returns its id, or None.

    The claim is a single UPDATE ... WHERE id=? AND status='queued';
    rowcount==1 proves this worker won the claim. Two workers racing
    for the same job cannot both get rowcount 1.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            select(ClipJob.id).where(ClipJob.status == "queued").order_by(ClipJob.id.asc()).limit(1)
        ).first()
        if row is None:
            return None
        job_id = int(row[0])
        result = db.execute(
            update(ClipJob)
            .where(ClipJob.id == job_id)
            .where(ClipJob.status == "queued")
            .values(status="analyzing", worker_id=WORKER_ID, updated_at=datetime.now(timezone.utc))
        )
        db.commit()
        if result.rowcount == 1:
            return job_id
        return None
    finally:
        db.close()


def billing_pass() -> int:
    """Refresh subscription state for users whose paid plan has expired.

    Returns the number of accounts moved back to Free. Never raises:
    a failure for one user is logged and the pass continues.
    """
    now = datetime.now(timezone.utc)
    changed = 0
    db = SessionLocal()
    try:
        from .services.billing import refresh_subscription_state

        users = db.scalars(
            select(User).where(
                User.plan.in_(("lite", "pro")),
                User.plan_ends_at.is_not(None),
                User.plan_ends_at <= now,
            )
        ).all()
        for user in users:
            try:
                if refresh_subscription_state(db, user, now):
                    changed += 1
                    _log(f"Subscription expired for user {user.id} ({user.username}); returned to Free.")
            except Exception as exc:  # one bad user must not stop the pass
                db.rollback()
                _log(f"Billing refresh failed for user {user.id}: {exc}")
        db.commit()
    except Exception as exc:
        db.rollback()
        _log(f"Billing pass failed: {exc}")
    finally:
        db.close()
    return changed


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _log(f"Started. Poll interval {WORKER_POLL_SECONDS}s. Press Ctrl+C or send SIGTERM to stop.")
    last_billing = 0.0
    while not _stop:
        try:
            if time.monotonic() - last_billing >= BILLING_PASS_INTERVAL_SECONDS:
                changed = billing_pass()
                if changed:
                    _log(f"Billing pass: {changed} expired subscription(s) refreshed.")
                last_billing = time.monotonic()
            job_id = claim_one_job()
            if job_id is not None:
                _log(f"Claimed job {job_id}; processing.")
                try:
                    process_job(job_id)
                except Exception as exc:  # process_job should not raise, but never kill the loop
                    _log(f"process_job({job_id}) raised unexpectedly: {exc}")
                _log(f"Finished job {job_id}.")
                continue  # check for another queued job immediately
        except Exception as exc:
            _log(f"Worker loop error (will retry next poll): {exc}")
        # Sleep in small slices so SIGTERM stops promptly.
        deadline = time.monotonic() + WORKER_POLL_SECONDS
        while not _stop and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))
    _log("Stopped cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
