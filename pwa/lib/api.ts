/**
 * Client-side API helper — every page talks to the FastAPI backend through
 * the Next.js server-side proxy at /api/v1/* (see lib/proxy.ts).
 * The browser never talks to FastAPI directly.
 *
 * Auth: session cookie (httpOnly, set by FastAPI, forwarded by the proxy).
 * CSRF: state-changing requests (POST/PUT/PATCH/DELETE) send the token from
 * GET /api/v1/csrf as the X-CSRF-Token header.
 */

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Thrown instead of ApiError on 401 so pages can redirect to /login. */
export class UnauthenticatedError extends ApiError {
  constructor(message = "Please sign in.") {
    super(401, message);
    this.name = "UnauthenticatedError";
  }
}

export function isUnauthenticated(err: unknown): boolean {
  return err instanceof UnauthenticatedError;
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Fetch a fresh CSRF token for the current session. */
export async function getCsrf(): Promise<string> {
  const res = await fetch("/api/v1/csrf", { credentials: "include" });
  if (!res.ok) {
    throw new ApiError(res.status, "Could not get a security token. Please try again.");
  }
  const data: unknown = await res.json().catch(() => null);
  const token =
    data && typeof data === "object" && "csrf_token" in data
      ? (data as { csrf_token?: unknown }).csrf_token
      : null;
  if (typeof token !== "string" || token.length === 0) {
    throw new ApiError(502, "Could not get a security token. Please try again.");
  }
  return token;
}

export interface ApiFetchOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** JSON-serialisable body, or FormData for multipart uploads. */
  body?: unknown;
  headers?: Record<string, string>;
}

/**
 * Call the backend via the Next.js proxy.
 * @example
 *   const data = await apiFetch<{ user: UserSummary }>("/me");
 *   await apiFetch("/auth/logout", { method: "POST" });
 */
export async function apiFetch<T = unknown>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { ...(options.headers ?? {}) };

  if (MUTATING_METHODS.has(method)) {
    headers["X-CSRF-Token"] = await getCsrf();
  }

  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    if (options.body instanceof FormData) {
      body = options.body; // browser sets the multipart boundary; never set Content-Type here
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.body);
    }
  }

  const url = `/api/v1${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(url, { method, headers, body, credentials: "include" });

  const contentType = res.headers.get("content-type") ?? "";
  const data: unknown = contentType.includes("application/json")
    ? await res.json().catch(() => null)
    : await res.text().catch(() => "");

  if (!res.ok) {
    if (res.status === 401) {
      throw new UnauthenticatedError(
        data && typeof data === "object" && "error" in data && typeof data.error === "string"
          ? data.error
          : "Please sign in."
      );
    }
    const message =
      data && typeof data === "object" && "error" in data && typeof data.error === "string"
        ? data.error
        : typeof data === "string" && data.length > 0
          ? data
          : `Request failed (${res.status}).`;
    throw new ApiError(res.status, message);
  }

  return data as T;
}

/**
 * Public backend base URL, for requests that must bypass the Vercel
 * proxy. Set NEXT_PUBLIC_FASTAPI_URL to the backend's public URL
 * (e.g. https://z-clips.ethiodeploy.app). Empty when unset — callers
 * must then fall back to the proxied /api/v1 path.
 */
export function getFastApiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_FASTAPI_URL ?? "";
  return raw.trim().replace(/\/+$/, "");
}

export interface UploadTokenResponse {
  upload_token: string;
  upload_url: string;
}

export interface DirectUploadResponse {
  job: { id: number };
}

/**
 * Upload a video file DIRECTLY to the backend, bypassing the Vercel
 * serverless proxy (which rejects request bodies over ~4.5 MB — that is
 * the "Request Entity Too Large / FUNCTION_PAYLOAD_TOO_LARGE" failure).
 *
 * Two steps:
 *  (a) POST /clips/upload-token via the proxy (small JSON request) to
 *      get a one-time token + the backend upload URL.
 *  (b) POST the FormData straight to that backend URL with
 *      credentials: "omit" — the session cookie is set for the Vercel
 *      domain and the browser will not send it cross-origin, so the
 *      upload token (sent as a form field) is the auth for this request.
 * No CSRF header is sent on step (b): the token itself is the auth.
 *
 * Only for file uploads. URL-source creates and every other API call
 * stay on the proxied apiFetch path.
 */
export async function directUploadClip(
  form: FormData
): Promise<DirectUploadResponse> {
  // The free backend sleeps after ~30 min idle. The first request wakes it,
  // but the direct upload can fail while it is still starting up. Retry the
  // whole two-step flow once after a short wait, with a FRESH token each
  // time (tokens are single-use, so a consumed/expired one cannot be reused).
  // If the phone cannot reach the backend directly at all (some networks
  // block it), fall back to the proxied upload — that works for files under
  // ~4.5MB, which covers short phone clips.
  let lastError: unknown = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, 8000));
    }
    try {
      return await directUploadClipOnce(form);
    } catch (err) {
      lastError = err;
      const isNetworkError =
        err instanceof ApiError && err.status === 502;
      if (!isNetworkError || attempt === 1) {
        break;
      }
      // Network error on the first attempt: the server was probably waking
      // up. Fall through to the retry.
    }
  }
  // Direct upload unreachable from this network. Try the proxied path
  // (works for files under ~4.5MB). A large file will fail there with the
  // size error, which is the honest result.
  try {
    return await apiFetch<DirectUploadResponse>("/clips", {
      method: "POST",
      body: form
    });
  } catch {
    throw lastError;
  }
}

async function directUploadClipOnce(
  form: FormData
): Promise<DirectUploadResponse> {
  const tokenRes = await apiFetch<UploadTokenResponse>("/clips/upload-token", {
    method: "POST",
    body: {}
  });
  const uploadToken = tokenRes?.upload_token;
  const uploadUrl = tokenRes?.upload_url;
  if (typeof uploadToken !== "string" || uploadToken.length === 0) {
    throw new ApiError(502, "Could not start the upload. Please try again.");
  }
  if (typeof uploadUrl !== "string" || uploadUrl.length === 0) {
    throw new ApiError(502, "Could not start the upload. Please try again.");
  }

  const directForm = new FormData();
  form.forEach((value, key) => {
    directForm.append(key, value);
  });
  directForm.set("upload_token", uploadToken);

  let res: Response;
  try {
    res = await fetch(uploadUrl, {
      method: "POST",
      body: directForm,
      credentials: "omit"
    });
  } catch {
    throw new ApiError(502, "Could not reach the upload server. Please try again.");
  }

  const contentType = res.headers.get("content-type") ?? "";
  const data: unknown = contentType.includes("application/json")
    ? await res.json().catch(() => null)
    : await res.text().catch(() => "");

  if (!res.ok) {
    if (res.status === 401) {
      throw new UnauthenticatedError(
        data && typeof data === "object" && "error" in data && typeof data.error === "string"
          ? data.error
          : "Please sign in."
      );
    }
    const message =
      data && typeof data === "object" && "error" in data && typeof data.error === "string"
        ? data.error
        : typeof data === "string" && data.length > 0
          ? data
          : `Upload failed (${res.status}).`;
    throw new ApiError(res.status, message);
  }
  return data as DirectUploadResponse;
}
