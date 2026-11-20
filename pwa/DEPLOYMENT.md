# Z CLIPS — Deployment Guide

Two parts, deployed together:

| Part | What it is | Where it runs |
|---|---|---|
| **Backend** | FastAPI app in `~/workspace/z-clips/Z_CLIPS_Project/` — auth, plans, credits, payments, clip pipeline, worker, owner admin, YouTube monitoring | A Python web service + a background worker process |
| **PWA (this project)** | Next.js 15 frontend in `~/workspace/z-clips/Z_CLIPS_PWA/` — mobile-first UI, PWA manifest/icons, service worker | A Node.js web service that proxies `/api/v1/*` to the backend |

The PWA never talks to the database directly. All business rules live in the
backend. The PWA only needs one setting: `FASTAPI_BASE_URL` (the backend's
public URL).

## What the owner must supply (nothing is faked)

1. **NayaPay QR image** — place the owner's real QR PNG at the backend's
   `storage/nayapay-qr.png` (or set `NAYAPAY_QR_PATH`). Until then the payment
   page shows "QR code not yet provided by the owner" and `/pay/qr` returns
   404. Never use a placeholder QR.
2. **YouTube Data API v3 key** (optional, Pro monitoring only) — set
   `YOUTUBE_API_KEY` on the backend. Without it, YouTube Monitoring shows
   "not configured" and never fakes live data.
3. **Captions (faster-whisper)** — not installed by default. After deploy,
   the owner installs it from Owner → System & Models (see the backend's
   `docs/MODEL_INSTALL.md`). Until then, clip jobs finish without captions
   and the UI shows the honest `needs_model` state.
4. **Secrets** — generate two long random strings (≥ 32 characters) and set
   them on the backend only: `SESSION_SECRET` and `OWNER_SETUP_TOKEN`.
   Never commit them. Never put them in the PWA.
5. **Owner account** — after the first deploy, open
   `<backend-url>/owner-setup` once with the `OWNER_SETUP_TOKEN` and a
   password (≥ 16 characters) to create the fixed `zclips_owner` account.
   This page closes permanently once an owner exists.

## Recommended free start (Render + Neon)

Free hosting always has a trade-off. Be honest about it with users:

- **Render free web services sleep after 15 minutes idle** and take about
  a minute to wake on the next visit. That is normal on the free tier.
- **Render free has no background workers.** The clip worker (which renders
  clips) cannot run as a free Render service. Options:
  - Start with the website only (signup, plans, payments, dashboard all
    work). Clip jobs will sit queued until a worker runs.
  - Run the worker on the owner's own computer when needed:
    `python -m app.worker` (it polls the database and renders queued jobs).
  - Upgrade later: a Render Starter background worker is $7/month and runs
    the worker 24/7.
- **Do not use SQLite on Render.** Render's filesystem is ephemeral — a
  SQLite file is lost on restart/redeploy. Use a free hosted PostgreSQL
  instead (the backend is PostgreSQL-ready via `DATABASE_URL` + Alembic):
  **Neon free tier** (persistent, no card required) is the recommended
  choice. Render's own free PostgreSQL expires after 30 days — do not use
  it for real user data.

### Steps (Render + Neon)

1. **Create a Neon free account** (neon.tech) → create a project → copy the
   PostgreSQL connection string. It looks like
   `postgresql://user:password@host/dbname?sslmode=require`.
2. **Put the code on GitHub** — create a free GitHub account, create two
   repositories (one for the backend, one for the PWA) and push the two
   project folders. (Render deploys from GitHub.)
3. **Create a Render free account** (render.com) → New → Web Service →
   connect the **backend** repo:
   - Runtime: Python. Build command: `pip install -r requirements.txt`.
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
   - Environment variables: `DATABASE_URL` (the Neon string),
     `SESSION_SECRET`, `OWNER_SETUP_TOKEN`, `APP_ENV=production`,
     `YOUTUBE_API_KEY` (when you have it), `PUBLIC_BASE_URL` (the backend's
     Render URL, e.g. `https://z-clips-backend.onrender.com`).
   - After the first deploy, run the database migration once (Render Shell):
     `alembic upgrade head`.
   - Note the backend's public URL.
4. **Create a second Render web service for the PWA** — connect the PWA repo:
   - Runtime: Node. Build command: `npm install && npm run build`.
   - Start command: `npm run start`.
   - Environment variable: `FASTAPI_BASE_URL` = the backend's public URL
     from step 3. (No secrets go in the PWA.)
5. **Create the owner account**: open `<backend-url>/owner-setup`, enter the
   `OWNER_SETUP_TOKEN` and a strong password. Then sign in at the PWA URL
   as `zclips_owner`.
6. **Upload the NayaPay QR** (see "What the owner must supply" above) and,
   when ready, add the YouTube API key and install faster-whisper from the
   Owner dashboard.
7. **Test**: sign up a test customer on the PWA, upload a short video, and
   confirm a clip job is created. (It will stay queued until a worker runs —
   see the worker note above.)

### Paid upgrade path (when ready)
- Render Starter web service ($7/month) for the backend: no sleeping.
- Render Starter background worker ($7/month): run
  `python -m app.worker` 24/7 so clips render automatically.
- A custom domain can be added on Render for either service.

## Environment variables

### Backend (`Z_CLIPS_Project/.env` — never commit)
| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | `sqlite:///./zclips.db` locally; `postgresql://...` in production |
| `SESSION_SECRET` | Yes | ≥ 32 random chars |
| `OWNER_SETUP_TOKEN` | Yes | ≥ 32 random chars |
| `APP_ENV` | No | `development` / `production` |
| `PUBLIC_BASE_URL` | No | Public backend URL (used in links) |
| `YOUTUBE_API_KEY` | No | YouTube Data API v3 key; empty = monitoring not configured |
| `NAYAPAY_QR_PATH` | No | Default `storage/nayapay-qr.png` |
| `MAX_UPLOAD_MB` | No | Default 2048 |
| `MAX_SCREENSHOT_MB` | No | Default 10 |

### PWA (`Z_CLIPS_PWA/.env.local` — never commit)
| Variable | Required | Notes |
|---|---|---|
| `FASTAPI_BASE_URL` | Yes | Backend base URL (e.g. `http://127.0.0.1:8000` locally, the Render URL in production) |
| `NEXT_PUBLIC_APP_URL` | No | Public PWA URL (e.g. `http://localhost:3000` locally) |
| `NEXT_PUBLIC_FASTAPI_URL` | Yes in production | Public backend URL the browser posts video files to DIRECTLY, bypassing the Vercel proxy (which rejects bodies over ~4.5 MB). Same value as the backend's public URL, e.g. `https://z-clips.ethiodeploy.app`. When unset, file uploads fall back to the proxied `/api/v1` path and large videos fail. |

## Run locally

```bash
# Backend (terminal 1) — from Z_CLIPS_Project/
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# Worker (terminal 2, optional — renders queued clip jobs)
.venv/bin/python -m app.worker

# PWA (terminal 3) — from Z_CLIPS_PWA/
npm install
FASTAPI_BASE_URL=http://127.0.0.1:8000 npm run dev   # http://localhost:3000
# Production build:
npm run build && FASTAPI_BASE_URL=http://127.0.0.1:8000 npm run start
```

## Verify a deploy

- `GET <pwa-url>/api/health` → `{"ok":true,"service":"z-clips-web"}`
- `GET <pwa-url>/api/v1/health` → `{"ok":true,"service":"z-clips"}` (via proxy)
- `GET <pwa-url>/manifest.webmanifest` → valid JSON with both icons
- Sign up → dashboard shows Free / 5 credits → upload a clip → job queued
  (renders once a worker runs) → approve → Downloads shows it.
- Payment submit → status Pending and the plan does NOT change until the
  owner approves it in Admin → Payments.

## PWA install (Android)

The PWA is installable when served over HTTPS with a valid manifest and
icons (both are included). On Android Chrome: open the PWA URL → menu →
"Add to Home screen" / "Install app". It then opens standalone (no browser
chrome). If offline, the app shell serves `/offline` with a retry button.
