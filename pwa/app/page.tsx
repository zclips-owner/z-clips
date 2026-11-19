import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const FASTAPI_BASE_URL = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * Root route: send signed-in users to the dashboard, everyone else to login.
 * The session check goes through the FastAPI backend server-side.
 * If the backend is unreachable we render a friendly message instead of crashing.
 */
export default async function Home() {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");

  // NOTE: redirect() works by throwing, so it must NOT sit inside the
  // try/catch below — a catch-all would swallow the redirect and render
  // the fallback instead. Only the fetch itself is guarded.
  let res: Response | null = null;
  try {
    res = await fetch(`${FASTAPI_BASE_URL}/api/v1/me`, {
      headers: cookieHeader ? { cookie: cookieHeader } : {},
      cache: "no-store"
    });
  } catch {
    res = null;
  }

  if (res === null) {
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col items-center justify-center px-4 text-center">
        <p className="text-2xl font-bold">
          Z <span className="text-purple-400">CLIPS</span>
        </p>
        <h1 className="mt-6 text-xl font-semibold">Backend unavailable</h1>
        <p className="muted mt-2 text-sm">
          Z CLIPS could not reach its server right now. Please check your connection and try again in a
          moment.
        </p>
        <a href="/login" className="primary-button mt-6 inline-block">
          Try again
        </a>
      </main>
    );
  }

  if (res.ok) {
    redirect("/dashboard");
  }
  redirect("/login");
}
