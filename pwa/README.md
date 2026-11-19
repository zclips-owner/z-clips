# Z CLIPS — PWA Frontend

Mobile-first installable PWA (Next.js 15 / React 19 / Tailwind CSS v3) for Z CLIPS.
**Frontend only** — it talks to the existing FastAPI backend through a server-side
proxy. All business logic (auth, plans, credits, clips, payments, YouTube
monitoring, admin) lives in the FastAPI backend; this app never implements it.

## How it works

- The browser only ever calls `/api/v1/*` on this Next.js app.
- `app/api/v1/[...slug]/route.ts` proxies every request server-side to
  `${FASTAPI_BASE_URL}/api/v1/*`, forwarding the session cookie, CSRF header
  and raw request body (including multipart uploads), and streaming the backend
  response (including file downloads and `Set-Cookie` headers) back.
- Client pages use `lib/api.ts` (`apiFetch`), which fetches a CSRF token from
  `/api/v1/csrf` for POST/PUT/PATCH/DELETE and throws `UnauthenticatedError`
  on 401 so pages can redirect to `/login`.
- API contract: `../API_CONTRACT.md` — build against the contract, not guesses.

## Run

```bash
npm install
cp .env.example .env.local   # set FASTAPI_BASE_URL to your backend
npm run dev                  # http://localhost:3000
```

Production:

```bash
npm run build
FASTAPI_BASE_URL=http://127.0.0.1:8000 npm run start
```

## Environment

| Variable | Purpose |
|---|---|
| `FASTAPI_BASE_URL` | FastAPI backend base URL (server-side only, default `http://127.0.0.1:8000`) |
| `NEXT_PUBLIC_APP_URL` | Public URL of this PWA (default `http://localhost:3000`) |

Never put secrets in `.env.example` or in the browser bundle.

## Structure

- `app/` — routes: `/` (session redirect), `/login`, `/signup`, `/offline`,
  `/manifest.webmanifest`, `/api/health`, `/api/v1/[...slug]` (proxy)
- `components/` — `app-shell.tsx`, `mobile-drawer.tsx`, `register-sw.tsx`
- `lib/` — `api.ts` (client helper), `proxy.ts` (server proxy), `plans.ts`
- `public/` — `sw.js` service worker, `icons/` PWA icons
