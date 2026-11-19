# Z CLIPS — Test Report

Date: 2026-11-19. Command: `.venv/bin/python -m pytest tests/ -q`
(Python 3.12.3, project venv). Run twice by two different agents with
the same result.

## Result

```
91 passed, 3 warnings in ~75s
```

Zero failures, zero errors. The 3 warnings are pre-existing and
non-fatal: the `httpx`/`starlette.testclient` deprecation notice and
the FastAPI `on_event` lifespan deprecation notice.

## What ran (91 tests)

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_auth.py` | 20 | Signup (success, smuggled role/plan/credits ignored, short username/password, duplicates), login (success, wrong password, missing/wrong CSRF → 403, 11th rapid login → 429), logout clears session, owner bootstrap (first success, second → 404, wrong token → 403, short password → 400), anonymous redirects, customer `/owner` → 403 |
| `tests/test_payments_flow.py` | 14 | `/pay` pricing (free → plans, Lite PKR 1,500, Pro PKR 5,250 promo), submit validation (missing txn/screenshot/bad extension → 400), valid Lite/Pro submits, promo seat reservation, waiting-page isolation (403/404), owner approve (Lite + 20 credits), double-approve → 400 with credits unchanged, reject releases promo seat, history scoped to own payments |
| `tests/test_clips_flow.py` | 12 | URL job deducts 1 credit, bad host → 400, Free + neon → 400, file upload deducts credit, zero-credit → 400 with exact message, `process_job` on a real 12s ffmpeg video → done/100/QC-passed clip on disk, approve → download 200 + counter, unapproved download → 403, job page 403 for another user, editor trim 2–5s re-render → ≈3s clip (DB + ffprobe), Free edit → 400, search scoped to own clips |
| `tests/test_support_yt.py` | 14 | Support create/reply, thread isolation (403 + hidden from lists), owner inbox + reply, customer reply reopens closed thread; YT Free locked, Free add → 403, Pro add → `waiting_authorized`, invalid input → 400, check with no API key → `not_configured`, authorize → `authorized`, delete own/other → 303/403 |
| `tests/test_owner_admin.py` | 11 | Customer 403 on all 6 owner pages, owner dashboard real stats, suspend → account unusable, plan change without/with `confirm=yes`, negative credit delta clamps at 0, extend +10 days, complimentary toggle, promotion `max_seats` clamp, QR upload, all 12 owner POSTs with invalid CSRF → 403 |
| `tests/test_qr_route.py` | 2 | `/pay/qr` → 404 with no file, 200 image when a PNG is at the configured path |
| `tests/test_billing.py` | 9 | First purchase, renewal carry-forward (7 + 20 = 27), double-approval refusal, plan-change refusal, reject releases seat, seats cannot oversell, approve converts seat, expired → Free, active not reverted |
| `tests/test_security.py` | 7 | CSRF (same/other session/secret/missing), rate limiter (allow/window/keys) |
| `tests/test_plans.py` | 2 | Plan definitions and entitlements |

## End-to-end verification (live server + worker, 2026-11-19)

Separate from pytest: the app was booted with real uvicorn
(`uvicorn app.main:app`, port 8471) and a real worker
(`python -m app.worker`) against a fresh SQLite DB and storage dir,
and driven over HTTP with httpx (script kept at `/tmp/zclips_e2e/e2e.py`):

```
44/44 checks passed
```

- Owner bootstrap → owner dashboard 200
- Customer signup → all 15 customer pages 200; customer `/owner` 403
- Payment submit (Lite, txn + screenshot) → waiting page → owner
  approve → Lite + 20 credits; double-approve → 400, credits unchanged
- Clip upload (real 30s test video) → worker claimed the job →
  `done`, 1 clip, QC passed, file on disk; ffprobe confirmed the
  render is **720×1280 (9:16), h264, 15.0s** with a thumbnail
- Approve → download 200 (~2.0 MB), downloads counter incremented
- All 7 owner pages 200; support thread created; Lite user blocked
  from adding a YT channel (403)

## Migration verification

- `alembic upgrade head` on a fresh DB creates all 16 tables.
- `alembic downgrade base` then `upgrade head` round-trips cleanly
  (both revisions: `468255d17319` initial schema,
  `7f3a1c9d2e4b` clip_jobs trim columns).
- `scripts/seed.py` runs and prints the honest config state
  (promotion active, QR not provided, YT key not configured, ffmpeg present).

## Notes / known test-harness quirks

- This Starlette's `TestClient` defaults `follow_redirects=True`;
  the suite's conftest creates it with `follow_redirects=False` so
  303 assertions are meaningful.
- The in-memory rate limiter is reset before every test (autouse
  fixture); rate-limit behaviour is tested explicitly within single
  tests.
- `process_job` uses its own `SessionLocal`, so clip tests monkeypatch
  `app.pipeline.run.SessionLocal` to the test sessionmaker.
- faster-whisper is NOT installed in this environment, so pipeline
  tests assert the honest "Transcription model not installed" log
  line and do not assert captions exist.
- Tests write real files under `storage/`; each test file has an
  autouse cleanup fixture that deletes only files/dirs created during
  that test (verified: `storage/` still contains only its original
  `clips/models/payments/uploads` dirs after the run).
- Empty-string form values are dropped by the httpx test client
  (verified live: `txn_id=""` → FastAPI 422); tests that need the
  app's 400 path send whitespace-only values instead. A real browser
  sends the empty value; this is a client-encoding quirk, not an app bug.
