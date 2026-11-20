# Z CLIPS — Route & Template Context Spec (CONTRACT)

This file is the contract between the router modules and the templates.
Routers MUST render the template named here with exactly these context
variables. Templates MUST use only these variables (plus `current_user`,
`is_owner`, `csrf_token`, which every page receives via `base_context`).

## Shared context (every page, via `app.deps.base_context`)
- `current_user`: the signed-in `User` or `None`
- `is_owner`: bool
- `csrf_token`: str — include as `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`
  in EVERY `<form method="post">`. POST routes validate it and return 403 on failure.
- `request`: the FastAPI Request (required by Jinja2/Starlette).

## Shared UI shell (templates/base.html)
- Sidebar hidden by default. A hamburger button (☰) sits BESIDE the search
  input in the topbar; clicking it opens a left slide-out sidebar, clicking
  it again (or the × in the sidebar) hides it. The profile name/avatar at
  top-right is NOT the sidebar toggle.
- Search input in the topbar: GET `/search?q=...` (searches the signed-in
  user's clips by title; owner search also finds users by username).
- Sidebar links (customer): Dashboard, Create New Clip, My Clips, Downloads,
  Subscription, View Plans, YT Monitoring, Help & Support, Settings, Logout.
  Owner sidebar adds: Owner Dashboard, Payment Requests, User Management,
  Support Inbox, Promotion, System & Models, Global Settings.
- Pages that require login redirect to `/login` (303). Owner-only pages
  return 403 for customers. Pro-only pages (YT Monitoring) return 403 for
  Free/Lite with a clear upgrade message.

## Auth routes (already in app/main.py — DO NOT rewrite)
- GET `/` landing (template `landing.html`, context `plans`)
- GET/POST `/login`, GET/POST `/signup` (template `auth.html`, context `mode`, `error`)
- GET/POST `/owner-setup` (template `owner_setup.html`, context `error`)
- GET `/logout`, GET `/health`
- GET `/plans` (template `plans.html`, context `plans`, `promotion`, `promo_active`)

## Customer routes (app/routers/customer.py)
| Method & path | Template | Context (in addition to shared) | Notes |
|---|---|---|---|
| GET `/dashboard` | `dashboard.html` | `user`, `entitlements`, `credits`, `remaining`, `plan_name`, `subscription` (Subscription or None), `promo_active`, `promotion`, `recent_activity` (list[Activity]), `yt_status` (str: locked/available/not_configured), `clip_counts` {total, approved, candidates} | No "Total Spent", no "Manage Subscription" button. Quick actions: Create Clip, Edit Video, Share. Cards: available clips, subscription end date, plan type, YT Monitoring status. "Need More Clips?" banner shown UNLESS promo_active. |
| GET `/clips` | `clips.html` | `clips` (list[Clip]), `jobs` (list[ClipJob]), `q` (search string) | My Clips: candidates + approved + rejected, with job status/progress. |
| GET `/clips/new` | `clip_new.html` | `entitlements`, `credits`, `caption_styles` (list of {key,name,description} allowed), `export_heights` (list[int]), `can_create` (bool), `error` | Upload (multipart video) OR source URL. Blocked with a clear message when credits == 0. |
| POST `/clips/new` | redirect → `/clips/jobs/{id}` | form: `title`, `caption_style`, `export_height`, `facecam_mode`, `aspect_ratio`, `layout`, `caption_size`, `caption_position`, `caption_bg`, `sticker_text`, `post_author`, `post_text`, `video` (file) OR `source_url`; `csrf_token` | Validates plan entitlements (caption style, export height; editor fields + manual facecam need Lite/Pro via `manual_editing`), deducts 1 credit transactionally, creates SourceVideo + ClipJob(status=queued). Rate limit bucket `clip_create`. |
| GET `/clips/jobs/{job_id}` | `job.html` | `job`, `clips` (list[Clip] for this job), `source` | Job status, progress bar, log lines, error. Owner may view any job; customer only own. |
| GET `/clips/{clip_id}` | `clip_editor.html` | `clip`, `job`, `entitlements`, `caption_styles`, `export_heights`, `can_edit` (bool), `error` | Editor: trim start/end (seconds), caption style, export height, facecam position (manual needs Lite/Pro via `manual_editing` — otherwise the fields are disabled with an upgrade note), aspect ratio, framing layout, caption size/position/background, sticker text, social-post author/text. Free users see "AI automatic only" and cannot edit (can_edit False). |
| POST `/clips/{clip_id}/edit` | redirect → `/clips/jobs/{new_job_id}` | form: `start_s`, `end_s`, `caption_style`, `export_height`, `facecam_mode`, `facecam_x/y/w/h`, `aspect_ratio`, `layout`, `caption_size`, `caption_position`, `caption_bg`, `sticker_text`, `post_author`, `post_text`; `csrf_token` | Only when can_edit. Creates a NEW ClipJob (re-render) for the same source; deducts 1 credit. Validates ranges (0 <= start < end <= source duration). |
| POST `/clips/{clip_id}/approve` | redirect → `/downloads` | `csrf_token` | Only the clip owner. Sets status=approved, approved_at=now, logs activity. |
| POST `/clips/{clip_id}/reject` | redirect → `/clips` | `csrf_token` | Only the clip owner. Sets status=rejected. |
| GET `/downloads` | `downloads.html` | `clips` (approved only) | Only approved clips appear here. Each row has a download link. |
| GET `/downloads/{clip_id}` | (file response) | — | Streams the clip file. Owner-or-owner check; 404 if missing; increments `clip.downloads`; logs activity. |
| GET `/subscription` | `subscription.html` | `user`, `entitlements`, `subscription` (active Subscription or None), `history` (list[Subscription]), `ledger` (list[CreditLedger]), `promo_active`, `promotion` | Current plan, credits, end date, renewal note, credit ledger, subscription history. Plan-change note: "Plan changes need owner confirmation — contact support." |
| GET `/settings` | `settings.html` | `user`, `message`, `error` | Own profile (display name, email — email change requires re-verification? No: email change is allowed, must stay unique), preferences (default caption style, default export height). |
| POST `/settings` | redirect → `/settings` | form: `display_name`, `email`, `default_caption_style`, `default_export_height`; `csrf_token` | Validates email uniqueness + caption style allowed for plan. |
| GET `/search?q=` | `search.html` | `q`, `clips` (list[Clip]), `users` (owner only, list[User]) | Searches own clips by title. |

## Payment routes (app/routers/payments.py)
- NayaPay constants (from app/config.py): holder "Zain ul abideen", number
  "03296195965", instruction "If scanning the QR code does not show the
  account holder name Zain ul abideen, use the NayaPay number 03296195965 instead."
  QR image: `NAYAPAY_QR_PATH`. If the file does NOT exist, show the explicit
  state "QR code not yet provided by the owner" (never a fake/placeholder QR).
| Method & path | Template | Context | Notes |
|---|---|---|---|
| GET `/pay?plan=lite\|pro` | `pay.html` | `plan`, `plan_info`, `amount_pkr`, `is_promotional`, `promo_active`, `promotion`, `qr_available` (bool), `qr_instruction`, `error` | Amount = promo price 5250 when Pro + promo active, else plan price. Free has no payment page (redirect to /plans). |
| POST `/pay` | redirect → `/pay/waiting/{id}` | form: `plan`, `txn_id`, `screenshot` (image file, ≤10MB); `csrf_token` | Validates plan, txn_id non-empty, screenshot is an allowed image. Reserves a promo seat transactionally when Pro+promo (if no seat, error "promotion seats are sold out"). Creates PaymentRequest(status=pending). Rate limit `payment_submit`. |
| GET `/pay/waiting/{payment_id}` | `pay_waiting.html` | `payment`, `plan_info` | Shows current status (pending/approved/rejected) and refreshes. Approved → link to /subscription. Rejected → shows review note + link to resubmit. Only the owner of the payment (or owner) may view. |
| GET `/payments` | `payments.html` | `payments` (own PaymentRequests, newest first) | Payment History. Never shows owner balance or other users' payments. |

## Support routes (app/routers/support.py)
| Method & path | Template | Context | Notes |
|---|---|---|---|
| GET `/support` | `support.html` | `threads` (own threads), `is_owner` | Help & Support: list own threads + "new request" link. |
| GET `/support/new` | `support_new.html` | `error` | Form: subject, message. |
| POST `/support/new` | redirect → `/support/{id}` | form: `subject`, `message`; `csrf_token` | Creates thread + first message (author_role=customer). |
| GET `/support/{thread_id}` | `support_thread.html` | `thread`, `messages` | Customer sees ONLY own threads (403 otherwise). Owner sees any. |
| POST `/support/{thread_id}/reply` | redirect → `/support/{thread_id}` | form: `message`; `csrf_token` | Author role = current user's role. Customer reply reopens a closed thread. |

## YouTube routes (app/routers/yt.py) — Pro only
| Method & path | Template | Context | Notes |
|---|---|---|---|
| GET `/yt` | `yt.html` | `channels` (own YTChannel list), `api_configured` (bool), `can_monitor` (bool), `error` | Free/Lite: locked card with upgrade message (page still renders, no 403 — the lock is shown in-page per the spec's "YT Monitoring card is locked"). Add-channel form (URL/handle). Each channel shows live status + ingestion state. |
| POST `/yt/channels` | redirect → `/yt` | form: `channel_url`; `csrf_token` | Pro only (403 otherwise). Validates a YouTube channel URL or @handle. ingestion_state starts "waiting_authorized". |
| POST `/yt/channels/{id}/check` | redirect → `/yt` | `csrf_token` | Pro only, own channel. Calls the live-status service (YouTube Data API when a key is configured; otherwise returns "not_configured" and does NOT pretend). After updating the channel row it calls the live-capture hook (`services/live_capture.on_live_status_change`): authorised channel live → start background segment capture; stream just ended → queue the VOD for clipping (only when the VOD is fetchable; otherwise retried on the next check). The hook never raises and never changes the check result. |
| POST `/yt/channels/{id}/delete` | redirect → `/yt` | `csrf_token` | Pro only, own channel. |
| POST `/yt/channels/{id}/authorize` | redirect → `/yt` | form: `note`; `csrf_token` | Pro only. Records that the user has authorised ingestion for this channel (a free-text note describing the authorised source, e.g. "my own channel, VOD file uploaded by me"). Sets ingestion_state="authorized". This does NOT download anything. |

## Owner routes (app/routers/owner.py) — owner only, all audited
| Method & path | Template | Context | Notes |
|---|---|---|---|
| GET `/owner` | `owner.html` | `user`, `stats` {users, active, suspended, active_subscriptions, pending_payments, approved_payments, revenue_pkr, clips_total, jobs_queued, jobs_processing}, `recent_payments` (pending list), `recent_activity` | Owner Dashboard (replaces the Stage 1 placeholder cards with real data). Revenue = sum of approved payment amounts. |
| GET `/owner/payments` | `owner_payments.html` | `payments` (all, filter by `status` query), `filter_status` | Admin Payment Requests. Each row: user, plan, amount, txn id, screenshot link, status, actions. |
| GET `/owner/payments/{id}` | `owner_payment_detail.html` | `payment`, `payer`, `classification` ("first_purchase"|"renewal"|"plan_change"), `plan_change_blocked` (bool), `screenshot_url` | Review detail. Approve is DISABLED with a clear reason when classification == plan_change. |
| POST `/owner/payments/{id}/approve` | redirect → `/owner/payments/{id}` | `csrf_token` | Calls billing.apply_payment (transactional). On ValueError, re-render detail with the error. Audited. |
| POST `/owner/payments/{id}/reject` | redirect → `/owner/payments` | form: `note`; `csrf_token` | Calls billing.reject_payment. Audited. |
| GET `/owner/users` | `owner_users.html` | `users` (list), `q` (search), `stats` | Admin User Management. Search by username/email. |
| GET `/owner/users/{id}` | `owner_user_detail.html` | `target`, `subscriptions`, `ledger`, `payments`, `activity`, `message`, `error` | User detail + all admin actions (each a POST below). |
| POST `/owner/users/{id}/status` | redirect → detail | form: `action` (activate|deactivate|suspend|unsuspend); `csrf_token` | Sets is_active / is_suspended. Cannot act on the owner account. Audited. |
| POST `/owner/users/{id}/plan` | redirect → detail | form: `plan`, `confirm` (must be "yes"); `csrf_token` | EXPLICIT plan change (the owner confirmation the spec requires). Sets plan + credits to the new plan allowance, ends_at = now+30d, logs + audits. Only with confirm=yes. |
| POST `/owner/users/{id}/credits` | redirect → detail | form: `delta` (int, may be negative), `reason`; `csrf_token` | Adjust credits (clamped at >= 0). Ledger entry + audit. |
| POST `/owner/users/{id}/extend` | redirect → detail | form: `days` (int 1-365); `csrf_token` | Extend plan_ends_at by N days. Audit. |
| POST `/owner/users/{id}/complimentary` | redirect → detail | form: `enable` (on/off); `csrf_token` | Grant/revoke Complimentary Premium. Audit. |
| GET `/owner/support` | `owner_support.html` | `threads` (all, open first) | Support Inbox. |
| GET `/owner/support/{thread_id}` | `support_thread.html` (same template) | `thread`, `messages` | Owner reply view. |
| POST `/owner/support/{thread_id}/reply` | redirect → thread | form: `message`; `csrf_token` | Owner reply. |
| POST `/owner/support/{thread_id}/close` | redirect → `/owner/support` | `csrf_token` | Close thread. |
| GET `/owner/promotion` | `owner_promotion.html` | `promotion`, `promo_active`, `message` | Promotion seat/expiry controls: max_seats, ends_at, is_active. Shows seats reserved/used/remaining. |
| POST `/owner/promotion` | redirect → `/owner/promotion` | form: `max_seats`, `ends_at` (datetime-local), `is_active`; `csrf_token` | Update promotion. Seats already reserved/used are never reduced below their current values (clamp). Audited. |
| GET `/owner/system` | `owner_system.html` | `storage` (storage_stats()), `worker` {status, queued, processing, failed, done_last_24h}, `models` (list[ModelInfo]), `ffmpeg_available` (bool), `db_url_scheme` | AI usage, server/storage/worker monitoring + model manager. |
| POST `/owner/system/models/{name}/install` | redirect → `/owner/system` | `csrf_token` | Starts an opt-in model install (background thread). See app/services/models_manager.py. |
| GET `/owner/settings` | `owner_settings.html` | `settings` (dict of AppSetting), `qr_available`, `message` | Global settings: NayaPay QR upload, support contact text, etc. |
| POST `/owner/settings` | redirect → `/owner/settings` | form: `qr` (optional image file), `support_email`, `announcement`; `csrf_token` | Saves QR file to NAYAPAY_QR_PATH when provided; stores other settings in AppSetting. |
| GET `/owner/payments/{id}/screenshot` | (file response) | — | Serves a payment screenshot to the owner only (never to customers). |

## Template inventory (17 pages)
landing, auth (login/signup), owner_setup, dashboard, clips (My Clips),
clip_new (Create New Clip), job (job status), clip_editor (Clip Editor),
downloads, subscription, plans (View Plans), pay (Payment Page),
pay_waiting (Payment Waiting), payments (Payment History), settings,
support (Help & Support list), support_new, support_thread, yt (YT Monitoring),
owner (Owner Dashboard), owner_payments (Admin Payment Requests),
owner_payment_detail, owner_users (Admin User Management),
owner_user_detail, owner_support, owner_promotion, owner_system,
owner_settings, search. Plus base.html (shell/sidebar) and a shared
`_flash` partial convention: pages show `message` (green) / `error` (red)
when present.
