import { NextRequest, NextResponse } from "next/server";

const FASTAPI_BASE_URL = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * Server-side proxy: forwards a /api/v1/* request to the FastAPI backend.
 *
 * - Forwards method, query string, Cookie header (zclips_session), the
 *   X-CSRF-Token header, and Content-Type.
 * - For non-GET requests forwards the raw request body bytes unchanged
 *   (required for multipart uploads — never re-encode FormData).
 * - Streams the backend response body back with its status, content-type,
 *   content-disposition, and any set-cookie headers.
 * - On FastAPI unreachable: 502 { "error": "Backend unavailable. Please try again." }
 * - Never logs secrets (no SESSION_SECRET, tokens, or Authorization values).
 */
export async function proxyToFastApi(
  req: NextRequest,
  slugParts: string[]
): Promise<NextResponse> {
  const slug = slugParts.join("/");
  const target = `${FASTAPI_BASE_URL}/api/v1/${slug}${req.nextUrl.search}`;

  const headers = new Headers();
  const cookie = req.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const csrf = req.headers.get("x-csrf-token");
  if (csrf) headers.set("x-csrf-token", csrf);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  let body: ArrayBuffer | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await req.arrayBuffer();
  }

  let backend: Response;
  try {
    backend = await fetch(target, {
      method: req.method,
      headers,
      body,
      // Do not follow redirects from the API: the contract says the API
      // never 303-redirects, and a 401 must reach the client as JSON.
      redirect: "manual"
    });
  } catch {
    return NextResponse.json({ error: "Backend unavailable. Please try again." }, { status: 502 });
  }

  const resHeaders = new Headers();
  const resContentType = backend.headers.get("content-type");
  if (resContentType) resHeaders.set("content-type", resContentType);
  const disposition = backend.headers.get("content-disposition");
  if (disposition) resHeaders.set("content-disposition", disposition);
  // Forward every Set-Cookie (session cookie) unchanged.
  for (const setCookie of backend.headers.getSetCookie()) {
    resHeaders.append("set-cookie", setCookie);
  }

  return new NextResponse(backend.body, { status: backend.status, headers: resHeaders });
}
