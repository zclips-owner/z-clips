# Z CLIPS — Current implementation status

Last updated: 2026-11-19. The full build is complete and verified
end-to-end (see `docs/TEST_REPORT.md`); it is **not** claimed
production-ready — see "Known limitations" below.

## Implemented (full build)
- **Auth & security:** Argon2id passwords, signed sessions, per-session
  CSRF tokens on every POST, sliding-window rate limits (login 10/min/IP,
  signup 5/10min/IP, payment 5/10min/user, clip create 10/min/user,
  generic POST 60/min/user), audit logs for all owner actions, activity
  feed for customers. Public signup always creates a customer; the owner
  (`zclips_owner`, lifetime Pro) exists only via the one-time bootstrap.
- **Plans & billing:** Free/Lite/Pro exactly as specified; credit
  carry-forward on same-plan renewal before expiry (transactional, no
  double crediting); Pro promotion (PKR 7,000 → 5,250, 20 seats, ends
  30 Nov 2026) with transactional seat reservation; plan changes while a
  paid subscription is active are refused by the payment approver and
  must be done explicitly by the owner in Admin → Users.
- **Clip pipeline:** upload (streamed to disk, 2 GB cap) or supported
  source URL (stored only, never downloaded); ffmpeg scene detection +
  audio-energy analysis; word-level transcription with Vosk (small
  English) — the lightweight engine that fits the 256 MB free tier
  (~210 MB peak for a 12 s clip, ~238 MB for 60 s; measured 2026-11-20),
  with faster-whisper as a fallback on hosts with >=1 GB RAM; never
  faked — honest "no captions" degradation when no engine is usable);
  9:16 rendering with ASS captions, loudness normalisation (-14 LUFS),
  direct cuts only; technical QC on every clip; job states
  queued → analyzing → rendering → qc → done/failed with logs, retries
  (max 3 attempts), idempotent re-runs; background worker with atomic
  job claiming; editor re-renders honour the trim range; approve/reject
  workflow; only approved clips in Downloads.
- **Payments (NayaPay, manual):** QR shown only when the owner supplied
  one (`GET /pay/qr`, 404 otherwise); exact account holder/number and
  QR instruction text; txn ID + screenshot submission; owner approve /
  reject with double-approval protection; waiting page; payment history
  scoped per customer (no owner balance shown to customers).
- **YouTube monitoring (Pro only):** live-status checks via YouTube Data
  API v3; honest `not_configured` state without an API key; authorised-
  source recording only — no downloading of public streams.
- **Live-stream clip extraction (Pro only, authorised channels):**
  `app/services/live_capture.py` — best-effort live capture (yt-dlp
  resolve + ffmpeg stream-copy segments, 10 min / 480p by default; each
  finished segment becomes a queued ClipJob for the existing worker)
  plus a VOD fallback (stream ended → VOD queued as a `url` job through
  the existing `download_source_url` path). Hooked into both live-check
  endpoints; `GET /api/v1/live/segments/{source_id}/moments` ranks a
  finished segment's moments with the pipeline's own scorer. Measured
  locally 2026-11-20: live capture works from this VM (NASA ISS live
  stream), ffmpeg record ~68 MB peak RSS; full pipeline run on a 30s
  segment peaked at ~342 MB (ffmpeg render step ~229–342 MB) — above
  the 256 MB free-tier container, so the worker needs a bigger host or
  a render memory limit before live capture is safe in production.
- **Support:** private threads per customer + owner only; customer
  replies reopen closed threads.
- **Owner admin:** stats, user management (status / plan / credits /
  extend / complimentary premium), payment review, promotion controls,
  support inbox, system monitoring (storage, worker, models, ffmpeg),
  global settings + NayaPay QR upload.
- **UI:** all 17 pages + owner admin pages (30 templates), sidebar
  hidden by default, hamburger beside search, profile top-right,
  search, promotion banner, "Need More Clips?" banner.
- **Migrations:** Alembic, two revisions (initial 16-table schema +
  clip_jobs trim columns); `alembic upgrade head` verified on a fresh DB.
- **Docs:** deployment guide, model install guide (disk/RAM/VRAM,
  licenses), security checklist, route/context spec, test report.

## Verification (actual results)
- Unit/integration suite: `pytest tests/` — **91 passed, 0 failed**
  (75s; see `docs/TEST_REPORT.md` for the per-file breakdown).
- End-to-end (live uvicorn + worker, 2026-11-19): 44/44 checks passed —
  owner bootstrap, signup, all customer/owner pages, payment submit →
  approve (Lite + 20 credits), double-approve refused, clip upload →
  worker render → done with QC-passed 720×1280 (9:16) 15.0s clip,
  approve → download (2.0 MB), support thread, YT gating for Lite.

## Known limitations (do NOT present as production-ready)
- Rate limiting is in-memory per process (a multi-worker deployment
  needs a shared store).
- No email/password recovery; owner password recovery is a shell-only
  script (`scripts/reset_owner_password.py`).
- SQLite is the dev default; use PostgreSQL in production.
- Uploads are not virus-scanned.
- Transcription works on the free tier via Vosk (small English): the
  model is downloaded once on first use (41 MB zip, checksum-verified)
  into `storage/models/vosk-small-en` and cached for the life of the
  container. faster-whisper remains available as a more accurate
  fallback on hosts with >=1 GB RAM (install from Owner → System &
  Models). Without any transcription engine, clips render without
  captions (honest degradation, never faked).
- YouTube live checks need a `YOUTUBE_API_KEY`; without it monitoring
  shows "not configured".
- NayaPay QR image not yet supplied — payment page shows the number
  instead (correct configuration state, not a bug).
- Single worker process; no CDN/object storage; no automated backups
  (see `docs/DEPLOYMENT.md` for the operator checklist).
- The `needs_model` job status is defined but never emitted: a missing
  transcription model degrades gracefully to caption-less clips with
  an honest log entry instead.
