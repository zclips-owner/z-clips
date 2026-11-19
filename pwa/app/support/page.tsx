"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/** GET /api/v1/support (list) + POST /api/v1/support (new thread). */

interface UserSummary {
  id: number;
  username: string;
  role: string;
}

interface SupportThread {
  id: number;
  subject: string;
  status: string;
  created_at: string;
  updated_at: string;
  username?: string;
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export default function SupportPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [threads, setThreads] = useState<SupportThread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await apiFetch<{ user: UserSummary }>("/me");
      setUser(me.user);
      const res = await apiFetch<{ threads: SupportThread[] }>("/support");
      setThreads(res.threads ?? []);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setError(err.message);
      else setError("Could not load your support requests. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (creating) return;
    setCreateError(null);
    if (!subject.trim()) {
      setCreateError("Please enter a subject.");
      return;
    }
    if (!body.trim()) {
      setCreateError("Please describe your issue.");
      return;
    }
    setCreating(true);
    try {
      const res = await apiFetch<{ thread: SupportThread }>("/support", {
        method: "POST",
        body: { subject: subject.trim(), body: body.trim() }
      });
      router.push(`/support/${res.thread.id}`);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setCreateError(err.message);
      else setCreateError("Could not create your request. Please try again.");
    } finally {
      setCreating(false);
    }
  }

  if (loading) {
    return (
      <AppShell isOwner={false}>
        <div className="flex flex-col items-center justify-center py-20">
          <div className="spinner" role="status" aria-label="Loading support requests" />
          <p className="muted mt-3 text-sm">Loading support requests…</p>
        </div>
      </AppShell>
    );
  }

  if (error) {
    return (
      <AppShell isOwner={user?.role === "owner"}>
        <h1 className="pt-2 text-xl font-bold">Help &amp; Support</h1>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </p>
        <button type="button" onClick={load} className="primary-button mt-4">
          Try again
        </button>
      </AppShell>
    );
  }

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <h1 className="pt-2 text-xl font-bold">Help &amp; Support</h1>
      <p className="muted mt-1 text-sm">Ask the owner for help — replies appear in your thread.</p>

      {!showForm ? (
        <button type="button" onClick={() => setShowForm(true)} className="primary-button mt-5 w-full">
          New request
        </button>
      ) : (
        <form onSubmit={handleCreate} className="panel mt-5 grid gap-4 p-5">
          <h2 className="font-bold">New request</h2>
          <label className="grid gap-1.5 text-sm font-medium">
            Subject
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              required
              placeholder="What do you need help with?"
            />
          </label>
          <label className="grid gap-1.5 text-sm font-medium">
            Message
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              required
              rows={5}
              placeholder="Describe your issue in detail…"
            />
          </label>
          {createError && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {createError}
            </p>
          )}
          <div className="flex gap-3">
            <button type="submit" className="primary-button flex-1" disabled={creating}>
              {creating ? "Sending…" : "Send request"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="btn-secondary"
              disabled={creating}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {threads.length === 0 ? (
        <div className="panel mt-5 p-6 text-center">
          <p className="font-semibold">No support requests yet</p>
          <p className="muted mt-1 text-sm">
            Tap “New request” above to start a conversation with the owner.
          </p>
        </div>
      ) : (
        <ul className="mt-5 grid gap-3">
          {threads.map((t) => (
            <li key={t.id} className="panel card p-4">
              <Link href={`/support/${t.id}`} className="block">
                <div className="flex items-center justify-between gap-3">
                  <strong className="text-sm">{t.subject}</strong>
                  <span
                    className={`badge ${
                      t.status.toLowerCase() === "open"
                        ? "border-green-500/50 bg-green-500/15 text-green-300"
                        : ""
                    }`}
                  >
                    {t.status.charAt(0).toUpperCase() + t.status.slice(1)}
                  </span>
                </div>
                <p className="muted mt-1 text-xs">Updated {fmtDateTime(t.updated_at)}</p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
