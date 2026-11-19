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
