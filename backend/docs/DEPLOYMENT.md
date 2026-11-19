# Z CLIPS — Deployment Guide

## 1. Local development

### Requirements
- Python 3.11+ (3.12 tested)
- FFmpeg + ffprobe on PATH (clip rendering and QC). Check with
  `ffmpeg -version` and `ffprobe -version`.
- SQLite (default) — no server needed for development.

### Steps
```bash
cd Z_CLIPS_Project
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt   # tests only
cp .env.example .env
# Edit .env: set SESSION_SECRET and OWNER_SETUP_TOKEN to two DIFFERENT
# long random values (at least 32 characters each).
.venv/bin/alembic upgrade head                 # create all tables
.venv/bin/uvicorn app.main:app --reload         # http://127.0.0.1:8000
```

### First owner setup
1. Open `http://127.0.0.1:8000/owner-setup`.
2. Enter the `OWNER_SETUP_TOKEN` from your private `.env`, then set the
   owner password (minimum 16 characters). The owner username is fixed:
   `zclips_owner`.
3. This endpoint closes permanently once an owner exists. There is no
   public owner signup and no password reset by email; if the owner
   password is lost, the operator must run a controlled, audited recovery
   (see `scripts/reset_owner_password.py` — it requires shell access and
   logs the action; it does not email anything).

### Background clip worker (separate process)
Clip rendering never runs inside web requests. Start the worker:
```bash
.venv/bin/python -m app.worker
```
The worker polls the `clip_jobs` table, claims queued jobs atomically and
processes them (analyse → render → QC). Run one worker per deployment for
SQLite; for PostgreSQL you may run several (claims are atomic).
Stop it with Ctrl-C / SIGTERM; it finishes the current job step, then exits.

### Running the tests
```bash
.venv/bin/python -m pytest -q
```
Tests use a temporary SQLite database (see `tests/conftest.py`) and never
touch the development database. See `docs/TEST_REPORT.md` for the latest
results.

## 2. Production deployment

### Requirements
- A Linux server (or container) with Python 3.11+, FFmpeg, and enough
  disk for video storage (see `docs/MODEL_INSTALL.md` for model sizes and
  `docs/PROJECT_STATUS.md` for current limits).
- PostgreSQL 14+ (strongly recommended for multi-user use; SQLite is for
  development only and does not support concurrent writers well).
- A reverse proxy (nginx / Caddy) terminating HTTPS.
- A process manager (systemd) for the web app and the worker.
- Automated backups (see section 4).

### Steps
1. Clone the project to the server. Create a dedicated system user
   (e.g. `zclips`) and give it ownership of the project + storage dirs.
2. Create the virtual environment and install requirements:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Create PostgreSQL database + user, then set `DATABASE_URL` in `.env`:
   ```
   DATABASE_URL=postgresql+psycopg2://zclips:<password>@localhost:5432/zclips
   ```
   (Install `psycopg2-binary` for the PostgreSQL driver:
   `.venv/bin/pip install psycopg2-binary`.)
4. Set in `.env`: `APP_ENV=production`, a long random `SESSION_SECRET`
   (≥32 chars), a long random `OWNER_SETUP_TOKEN` (≥32 chars),
   `PUBLIC_BASE_URL=https://your-domain.example`, `TRUST_PROXY=true`
   (only when behind a trusted reverse proxy), and `YOUTUBE_API_KEY`
   only if you want live-status checks.
5. Run migrations (never rely on `create_all` in production):
   ```bash
   .venv/bin/alembic upgrade head
   ```
6. Run the owner bootstrap once (`/owner-setup`) with the setup token.
7. Put the NayaPay QR image at the path in `NAYAPAY_QR_PATH`
   (default `./storage/nayapay-qr.png`) or upload it in
   Owner → Global Settings. Until then the payment page honestly states
   that the QR has not been provided.
8. Install the AI models you need from Owner → System & Models (or see
   `docs/MODEL_INSTALL.md`). Without the transcription model, clip jobs
   report a clear "model not installed" limitation instead of faking
   captions.
9. Start the web app and the worker as systemd services (examples below).
10. Configure the reverse proxy to forward to the app (HTTPS only, with
    `X-Forwarded-For` / `X-Forwarded-Proto`). The app sets secure cookies
    (`https_only`) automatically when `APP_ENV=production`.

### systemd examples
`/etc/systemd/system/zclips-web.service`:
```ini
[Unit]
Description=Z CLIPS web app
After=network.target postgresql.service

[Service]
User=zclips
WorkingDirectory=/opt/zclips/Z_CLIPS_Project
EnvironmentFile=/opt/zclips/Z_CLIPS_Project/.env
ExecStart=/opt/zclips/Z_CLIPS_Project/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
`/etc/systemd/system/zclips-worker.service` — same, but:
```ini
ExecStart=/opt/zclips/Z_CLIPS_Project/.venv/bin/python -m app.worker
```
Enable and start both, then check `systemctl status zclips-web zclips-worker`
and the `/health` endpoint.

### nginx example (reverse proxy)
```nginx
server {
    listen 443 ssl;
    server_name your-domain.example;
    ssl_certificate /etc/letsencrypt/live/your-domain.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.example/privkey.pem;
    client_max_body_size 2048M;   # must allow the largest video upload
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }
}
```

## 3. Backups and restore

What to back up:
- The database (PostgreSQL: `pg_dump`; SQLite: copy the `.db` file while
  the app is stopped, or use SQLite's `.backup` command).
- The `storage/` directory (uploaded sources, rendered clips, payment
  screenshots, NayaPay QR image, models).

```bash
# PostgreSQL
pg_dump -U zclips -d zclips -F c -f zclips-$(date +%F).dump
# SQLite (stop the app + worker first, or use .backup)
sqlite3 zclips.db ".backup zclips-$(date +%F).db"
# Storage
tar -czf storage-$(date +%F).tar.gz storage/
```
Restore: restore the database dump, restore `storage/` to the same path,
run `alembic upgrade head` to confirm the schema version, then restart
the web app and the worker. Verify with `/health` and the owner System page.

## 4. What is NOT configured automatically
- Domain, TLS certificate, hosting, and the reverse proxy.
- The NayaPay QR image (owner-supplied).
- The YouTube Data API key (optional; without it live-status checks are
  honestly reported as "not configured").
- AI model downloads (opt-in, owner-triggered; see `docs/MODEL_INSTALL.md`).
- GPU hardware: local AI runs on CPU unless the operator provides a GPU;
  the app never claims to create GPU capacity.

## 5. Production readiness checklist (do not skip)
- [ ] `APP_ENV=production`, strong `SESSION_SECRET` and `OWNER_SETUP_TOKEN`.
- [ ] PostgreSQL in use; `alembic upgrade head` applied; backup tested.
- [ ] HTTPS via a reverse proxy; `TRUST_PROXY=true` only behind it.
- [ ] Owner account created via `/owner-setup`; setup token stored offline.
- [ ] NayaPay QR image provided (or the "not provided" state accepted).
- [ ] Worker running (`systemctl status zclips-worker`); a test clip job
      completes end-to-end and passes QC.
- [ ] `pytest` passes on the deployment checkout.
- [ ] Storage disk has room for uploads + renders (monitor Owner → System).
