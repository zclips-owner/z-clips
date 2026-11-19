# Z CLIPS — MASTER IMPLEMENTATION PROMPT FOR MUSE

You are implementing a real SaaS product, not a static mockup. Read the entire project,
README, existing source code, and this document before changing anything.

## Non-negotiable working rules
1. Preserve the product decisions below. Do not invent, remove, rename, or silently alter features, prices, limits, design, or business rules.
2. Inspect the existing code first. Keep the working architecture where sound; refactor only when required and explain the reason.
3. Never use fake buttons, dummy data presented as real, simulated payment verification, or UI-only security.
4. Implement real backend validation and server-side authorization for every protected action.
5. If a feature cannot be completed because credentials, authorized media access, hardware, or infrastructure are missing, implement a clear configuration state and document the blocker. Never pretend it works.
6. Build incrementally. After each module, run tests, lint/type checks, and report actual results. Fix failures before moving on.
7. Do not claim production readiness until deployment tests, security checks, backups, worker tests, and end-to-end tests pass.
8. Do not put secrets, passwords, tokens, or API keys in source code, browser bundles, prompts, logs, or version control.

## Brand and UI
- Product: Z CLIPS
- Tagline: Create · Edit · Share
- Typography: Outfit throughout the application.
- Visual system: dark black/navy, purple neon, electric blue, premium gradients/glass cards, professional icons, restrained smooth animations.
- Desktop-first responsive layout.
- Sidebar hidden by default. Hamburger beside search opens a left slide-out sidebar. Clicking hamburger/close hides it.
- Profile name/avatar remains at top-right; it must not act as the sidebar toggle.
- All 17 pages must be real functional pages: Login/Signup, Dashboard, My Clips, Clip Editor, Downloads, Subscription, View Plans, Payment Page, Payment Waiting, Payment History, Settings, Help & Support, Create New Clip, YT Monitoring, Owner Dashboard, Admin Payment Requests, Admin User Management.

## Accounts and security
- One application and one database, with two access classes.
- Owner username: zclips_owner.
- Owner gets lifetime premium and no payment requirement.
- Owner account is created only through a one-time secure bootstrap flow or controlled deployment command. Public signup must always create role=customer. Never allow role, plan, credits, or admin permissions to be set from public request data.
- Owner password is chosen by the owner during setup, never hardcoded. Store only a strong password hash (Argon2id or equivalent).
- Protect all owner routes and actions server-side; enforce least privilege, CSRF protection, secure cookies, rate limits, audit logs, and safe recovery.
- Public customers create their own accounts and log in with their own credentials.
- Customer data and support conversations must be isolated by account.

## Plans — exact latest decisions
- Free: PKR 0, 5 clips/month, AI automatic clips only, no manual editing, one simple caption style, no YouTube Live monitoring.
- Monthly Lite: PKR 1,500/month, 20 clips/month, AI-assisted/manual editing, 2–3 caption styles, no YouTube Live monitoring.
- Monthly Pro: PKR 7,000/month, 70 clips/month, AI-assisted/manual editing, all caption styles, 4K export, YouTube Live monitoring, advanced manual facecam/video positioning.
- Quality must not be intentionally degraded on Free. All plans should receive good automatic editing, captions, framing and technical QC. Paid plans add clip capacity and creative controls, not deliberately bad Free output.
- Credit carry-forward: if a customer renews the same plan before expiry, unused credits carry forward and new plan credits are added; subscription validity restarts for 30 days. Implement transactionally and prevent double crediting. Plan-change behavior must be explicitly confirmed before implementation; do not invent it.
- Pro promotion: normal price PKR 7,000; 25% off = PKR 5,250; 20 seats maximum; ends 30 Nov 2026 or earlier when seats are consumed. Backend must reserve seats transactionally. Promotional price applies to first 30-day promotional Pro subscription; renewal at normal price unless a new offer is explicitly configured. Clearly display this condition.
- The dashboard displays the promotion while active; after expiry or sold-out, restore “Need More Clips?” banner.

## Clip workflow
- Upload a source video or submit a supported source URL.
- Analyze video and audio; find candidate moments; score them for context, interest, emotional/reaction signals, and completeness.
- Detect strong opening hooks without cutting away necessary context.
- Create vertical 9:16 clips with synchronized captions, audio normalization, facecam/gameplay layouts, and clean transitions (direct cuts; no fade transitions).
- Automatic facecam placement is available to all plans; manual positioning is Pro.
- Save generated candidates to My Clips.
- User can edit where plan allows. User explicitly approves clips; only approved clips appear in Downloads.
- No false promise of virality. Promise a careful, professional clipping/editing workflow, not guaranteed views or engagement.
- Every clip must pass technical QC: valid duration, playable file, no unintended black/frozen frames, audio present where expected, caption sync, aspect ratio, render completion, duplicate checks. Failed jobs should retry safely or report a useful error.
- Maintain job states, logs, retry handling, storage cleanup, and user-visible progress.

## AI and local processing
- User requires free/open-source, self-hosted tools only; do not add mandatory paid APIs or paid AI subscriptions.
- Evaluate licenses and compatibility before selecting models. Do not download arbitrary binaries blindly.
- Use a model manager/installer that checks hardware, OS, disk space, model license, checksums where available, versions and dependencies. Provide opt-in model downloads and clear progress; do not silently download huge models without explaining storage needs.
- Candidate tools may include FFmpeg, faster-whisper/Whisper, Silero VAD, OpenCV, PySceneDetect and suitable permissively licensed local video-language/vision models. Verify current license and actual capability before choosing.
- Distinguish CPU/GPU capability and warn that local AI processing needs compute, storage and memory. Do not claim installation can create GPU capacity.
- Do not use paid API fallbacks. If a suitable model cannot run on available hardware, show a transparent limitation.
- Use a background worker/queue for video processing; never block web requests during rendering.
- For YouTube: public live status detection is separate from authorized video ingestion. Do not download or process arbitrary public streams without permission/authorized source access. Show a truthful waiting-for-authorized-source state.

## Payments
- NayaPay account holder: Zain ul abideen.
- NayaPay number: 03296195965.
- Display the supplied QR code when it is provided to the project.
- Show exact instruction below QR: “If scanning the QR code does not show the account holder name Zain ul abideen, use the NayaPay number 03296195965 instead.”
- Label: “NayaPay Number: 03296195965”.
- Customer submits transaction ID and payment screenshot.
- Owner manually verifies actual incoming payment in NayaPay app and approves/rejects in Owner Dashboard.
- Do not claim automatic verification or webhook access for a personal NayaPay account unless officially supported and configured.
- Customer waiting page updates when owner decision is recorded.
- Never show owner balance or private transaction history to customers.

## Dashboard
- Quick actions: Create Clip, Edit Video, Share.
- Cards: available clips, subscription end date, plan type, YT Monitoring status.
- Recent activity: clip creation, approvals, downloads, renewals and relevant live activity.
- Remove Total Spent and old Manage Subscription dashboard button/card.
- YT Monitoring card is locked for Free/Lite and available for Pro.
- Search searches clips.
- Need More Clips banner appears except while promotion is active.

## Owner controls
- User totals, active subscriptions, pending/approved payments, revenue.
- Activate/deactivate/suspend users.
- Plan changes, credit adjustments, subscription extension.
- Payment approve/reject.
- Complimentary Premium.
- Support inbox/replies.
- Promotion seat/expiry controls.
- AI usage, server/storage/worker monitoring.
- All owner actions audited.

## Support and settings
- Customer support messages are private to that customer and owner; customers cannot see each other's conversations.
- Customer Settings contain only their own profile/preferences. Global settings, API/service configuration, users and payments are owner-only.

## Required implementation sequence
1. Audit the starter project and produce a file-by-file implementation plan.
2. Complete authentication, owner bootstrap, authorization, database migrations, CSRF and rate limits.
3. Implement shared UI shell, navigation, search and all page routes.
4. Implement plans, subscription lifecycle, credits, renewal and promotion transaction logic.
5. Implement clip upload, storage, job queue, AI analysis, render pipeline and QC.
6. Implement My Clips, editor entitlements, approval and Downloads.
7. Implement NayaPay manual payment workflow and owner review.
8. Implement YouTube channel/live status monitoring with authorized-source separation.
9. Implement support, settings, activity feed and owner administration.
10. Add automated tests, end-to-end tests, accessibility, responsive checks, security review, deployment guide and backup/restore instructions.

## Required deliverables from Muse
- Complete source project with no missing files.
- Database migrations and seed/bootstrap instructions.
- `.env.example` with placeholders only.
- Local development and production deployment guides.
- Model installation guide with disk/RAM/VRAM estimates and licenses.
- Test suite and actual test report.
- Security checklist and known limitations.
- No fake claims. Clearly mark anything requiring operator credentials, user-provided QR image, authorized media source, domain, hosting or hardware.
