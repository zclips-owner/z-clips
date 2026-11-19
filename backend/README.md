# Z CLIPS — Create · Edit · Share

A self-hosted SaaS that turns long videos into vertical 9:16 clips with
synced captions, built entirely on free/open-source local tooling
(FFmpeg, faster-whisper, OpenCV, PySceneDetect). No paid APIs.

## Stack
- **Web:** FastAPI + Jinja2 + SQLAlchemy, SQLite for local dev
  (PostgreSQL-ready via `DATABASE_URL`), Alembic migrations
- **Auth:** Argon2id passwords, signed sessions, per-session CSRF tokens,
  sliding-window rate limits, audit logs
- **Pipeline:** background worker (`python -m app.worker`) — ffmpeg scene
  detection + audio energy analysis, optional faster-whisper transcription,
  9:16 rendering with ASS captions and loudness normalisation, technical QC
  on every clip (duration, playable, black/frozen frames, audio, caption
  sync, aspect ratio, duplicates)
- **UI:** Outfit font, dark black/navy + purple neon + electric blue,
  glass cards, desktop-first. Sidebar hidden by default; the hamburger
  beside the search opens it. 17 real functional pages.

## Plans
| Plan | Price (PKR/mo) | Clips/mo | Caption styles | YT monitoring |
|------|----------------|----------|----------------|---------------|
| Free | 0 | 5 | simple | — |
| Lite | 1,500 | 20 | simple, bold, karaoke | — |
| Pro  | 7,000 | 70 | all (incl. minimal, neon) | yes + 4K export |

Renewing the same plan before expiry carries unused credits forward.
**Pro promotion:** 25% off (PKR 5,250), max 20 seats, ends 30 Nov 2026 —
seats are reserved transactionally at payment submission. Plan changes
while a paid subscription is active are NOT applied automatically; the
owner must confirm them explicitly in Admin → Users.

## Payments — NayaPay (manual)
Customers pay via NayaPay (account holder **Zain ul abideen**, number
**03296195965**) and submit the transaction ID + a payment screenshot.
The owner verifies in the NayaPay app and approves/rejects in the Owner
Dashboard. There is **no automatic verification** — nothing is claimed
that the app does not do. The QR code is shown only when the owner has
uploaded one (Owner → Global Settings); until then the payment page
shows a clear "QR code not yet provided" state and the number instead.

## YouTube Live monitoring (Pro only)
Public live-status detection via the YouTube Data API v3 (needs a
`YOUTUBE_API_KEY`). Live-status detection is **separate** from video
ingestion: the app never downloads or processes arbitrary public
streams. A channel stays in a truthful "waiting for authorised source"
state until the owner/user records an authorised source.

## Run locally
1. Python 3.12. Create a virtual environment:
   `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and set a long random `SESSION_SECRET`
   (≥32 chars) and `OWNER_SETUP_TOKEN` (≥32 chars).
3. Create the database: `.venv/bin/alembic upgrade head`
   (or just start the app — it creates missing tables on startup)
4. Start the web app: `.venv/bin/uvicorn app.main:app --reload`
5. Start the worker (separate terminal): `.venv/bin/python -m app.worker`
6. Open http://127.0.0.1:8000

## First owner setup
Open `/owner-setup`. Enter the `OWNER_SETUP_TOKEN` from your private
`.env` file, then set your owner password (≥16 characters). The owner
username is fixed: `zclips_owner`. This endpoint is one-time: once an
owner exists it returns 404. Public signup always creates a customer —
role/plan/credits can never be smuggled in through a form.

## Configuration (`.env`)
See `.env.example` for every setting (placeholders only — never put real
secrets in source). Notable: `DATABASE_URL`, `STORAGE_DIR`,
`NAYAPAY_QR_PATH`, `YOUTUBE_API_KEY`, `WORKER_POLL_SECONDS`,
`MAX_UPLOAD_MB` (default 2048), `MAX_SCREENSHOT_MB` (default 10).

## Docs
- `docs/DEPLOYMENT.md` — local dev + production deployment (systemd,
  nginx, PostgreSQL, backups/restore)
- `docs/MODEL_INSTALL.md` — AI model installation: disk/RAM/VRAM
  estimates and licenses
- `docs/SECURITY.md` — security checklist + known limitations
- `docs/TEST_REPORT.md` — what was tested and the actual results
- `docs/ROUTE_CONTEXT_SPEC.md` — the route/template contract
- `docs/PROJECT_STATUS.md` — honest current status

## Tests
`.venv/bin/python -m pytest tests/ -q`

## Production — read this first
Use PostgreSQL, HTTPS, secure cookies, a reverse proxy, persistent
private storage, automated backups, rate limiting, monitoring, and a
separate worker process. Do not deploy the development SQLite database
as a multi-user production database. The app is **not** claimed
production-ready: see `docs/SECURITY.md` and `docs/PROJECT_STATUS.md`
for the known limitations (in-memory rate limiting is per-process, no
password recovery by email, no virus scanning of uploads, transcription
needs the faster-whisper model installed by the owner).

Never put real passwords, setup tokens, API keys, or secrets in source
code, prompts, logs, or version control. Do not commit `.env`.
