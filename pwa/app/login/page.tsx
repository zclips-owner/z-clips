"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;
    setError(null);
    setLoading(true);
    try {
      await apiFetch("/auth/login", { method: "POST", body: { username, password } });
      router.push("/dashboard");
      router.refresh();
    } catch (err) {
      if (isUnauthenticated(err)) {
        setError("Username ya password sahi nahi hai.");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col items-center justify-center px-4">
      <Link href="/dashboard" className="mb-8 text-2xl font-bold tracking-wide">
        Z <span className="text-purple-400">CLIPS</span>
      </Link>
      <div className="panel w-full max-w-sm p-6">
        <h1 className="text-xl font-bold">Welcome back</h1>
        <p className="muted mt-1 text-sm">Sign in to your Z CLIPS account.</p>
        <form onSubmit={handleSubmit} className="mt-6 grid gap-4">
          <label className="grid gap-1.5 text-sm font-medium">
            Username
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              placeholder="Your username"
            />
          </label>
          <label className="grid gap-1.5 text-sm font-medium">
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              placeholder="Your password"
            />
          </label>
          {error && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}
          <button type="submit" className="primary-button w-full" disabled={loading}>
            {loading ? "Signing in…" : "Login"}
          </button>
        </form>
        <p className="muted mt-5 text-center text-sm">
          No account yet?{" "}
          <Link href="/signup" className="font-semibold text-purple-300">
            Sign up
          </Link>
        </p>
        <p className="muted mt-2 text-center text-sm">
          <Link href="/plans" className="text-purple-300">
            View plans
          </Link>
        </p>
      </div>
    </main>
  );
}
