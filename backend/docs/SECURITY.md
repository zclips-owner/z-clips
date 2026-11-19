# Z CLIPS — Security Checklist & Known Limitations

## Security measures implemented
- **Passwords:** Argon2id hashing (argon2-cffi). No plaintext passwords are
  stored or logged. Owner password minimum 16 chars; customer minimum 12.
- **Owner bootstrap:** one-time only, gated by a private `OWNER_SETUP_TOKEN`
  (≥32 chars in production). Public signup always creates `role=customer`;
  role/plan/credits are never accepted from request data.
- **Sessions:** signed server-side sessions (itsdangerous via Starlette
  `SessionMiddleware`), `zclips_session` cookie, `SameSite=lax`,
  `https_only` in production, 7-day expiry.
- **CSRF:** every state-changing POST carries a per-session signed token
  (`csrf_token`); the server validates it and returns 403 on failure.
- **Rate limiting:** sliding-window limits on login (10/min/IP), signup
  (5/10 min/IP), owner-setup (5/10 min/IP), payment submit (5/10 min/IP),
  clip creation (10/min/user) and generic POSTs (60/min/user). In-memory
  (single process). For multi-worker production, replace with a shared
  store (Redis) — see "Known limitations".
- **Authorization:** every protected route checks the signed-in user
  server-side; owner routes require `role=owner`; Pro routes check the
  plan entitlements; object ownership is checked on every clip/job/source/
  payment/support thread (customers can only touch their own rows).
- **Audit log:** owner actions (payment approve/reject, user status/plan/
  credit/extension/complimentary changes, promotion changes, model installs)
  and the owner bootstrap are written to `audit_logs`.
- **Uploads:** video/image extensions are allow-listed, file sizes are
  capped (uploads 2 GB, screenshots 10 MB), files are streamed to disk
  (never fully loaded into memory), stored outside the web root under
  per-user directories, and served only after an ownership check.
- **Secrets:** no secrets in source code, templates, logs or version
  control. `.env.example` contains placeholders only. `.env` is gitignored.
- **Payments:** no automatic verification is claimed. NayaPay payments are
  verified manually by the owner in the NayaPay app; the owner approves or
  rejects in the dashboard. Customer payment screenshots are served only
  to the owner. Owner balance / private transaction history is never shown
  to customers.
- **YouTube:** live-status detection only reads public status via the
  YouTube Data API (when a key is configured). No arbitrary public stream
  is downloaded or processed. Ingestion only happens from an authorised
  source the user provides, recorded explicitly.

## Security checks performed (see docs/TEST_REPORT.md)
- Automated tests cover: signup always creates a customer (role cannot be
  smuggled in), owner bootstrap closes after first use, CSRF rejection on
  missing/invalid tokens, rate-limit 429s, cross-user access 403s,
  owner-only route 403s for customers, promotion seat atomicity, payment
  double-approval refusal, and plan-change approval refusal.
- `pytest` must pass before any deployment claim.

## Known limitations (honest, do not present as solved)
1. **Rate limiting is in-memory and per-process.** With `--workers 2` (or
   several app servers) each process has its own counters, so an attacker
   gets N× the limit. Use a shared store (Redis) for production.
2. **No email / password recovery.** There is no email provider configured,
   so there is no "forgot password" flow. Owner recovery is a controlled
   shell command (`scripts/reset_owner_password.py`) that requires server
   access and is audited. Customers who lose a password must contact the
   owner (support), and the owner can reset it from Admin User Management
   only by setting a new password — there is no self-service reset.
   (Owner password reset from the admin panel is NOT implemented; see 3.)
3. **Owner cannot reset a customer's password from the admin panel yet.**
   The admin can deactivate/suspend a user; password reset requires the
   shell recovery script. Documented as a gap.
4. **SQLite is development-only.** Concurrent writers can hit
   "database is locked". Use PostgreSQL in production.
5. **No virus scanning of uploads.** Uploaded videos/screenshots are
   validated by extension and probed with ffprobe, but not scanned for
   malware. Run uploads through a scanner (e.g. ClamAV) if you accept
   untrusted files in production.
6. **YouTube live status needs an API key.** Without `YOUTUBE_API_KEY`,
   YT Monitoring reports "not configured". Live-status polling is not a
   background service yet — checks happen on demand (manual "Check now").
7. **Transcription needs the faster-whisper model.** Without it, clip jobs
   report "model not installed" and render without a caption track (never
   fake captions). See `docs/MODEL_INSTALL.md`.
8. **Facecam auto-placement is heuristic.** Automatic facecam uses the
   full-frame layout (no separate facecam track is fabricated); manual
   positioning (Pro) overlays a crop of a region the user selects. True
   face detection is not bundled.
9. **Single-worker rendering.** One worker process renders one job at a
   time. Large/4K renders are slow on CPU-only hardware; queue times grow
   with concurrent users. Scale workers only with PostgreSQL.
10. **No CDN / object storage.** Clips are served from local disk. For
    production scale, move `storage/` to object storage and serve via a CDN.

## Operator responsibilities
- Keep `SESSION_SECRET` and `OWNER_SETUP_TOKEN` private and long.
- Keep the server, Python packages and FFmpeg patched.
- Back up the database and `storage/` regularly and test restores.
- Review `audit_logs` (Owner → System) for unusual admin activity.
- Provide the NayaPay QR image and verify payments in the NayaPay app —
  never approve a payment you have not actually received.
