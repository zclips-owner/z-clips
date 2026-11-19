"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/** GET /api/v1/support/{threadId} + POST /api/v1/support/{threadId}/reply */

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

interface SupportMessage {
  id: number;
  thread_id: number;
  author_id: number;
  author_role: string;
  body: string;
  created_at: string;
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

export default function SupportThreadPage() {
  const router = useRouter();
  const params = useParams();
  const threadId = Array.isArray(params.threadId) ? params.threadId[0] : params.threadId;

  const [user, setUser] = useState<UserSummary | null>(null);
  const [thread, setThread] = useState<SupportThread | null>(null);
  const [messages, setMessages] = useState<SupportMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!threadId) return;
    setLoading(true);
    setError(null);
    try {
      const me = await apiFetch<{ user: UserSummary }>("/me");
      setUser(me.user);
      const res = await apiFetch<{ thread: SupportThread; messages: SupportMessage[] }>(
        `/support/${threadId}`
      );
      setThread(res.thread);
      setMessages(res.messages ?? []);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setError(err.message);
      else setError("Could not load this conversation. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [threadId, router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleReply(e: React.FormEvent) {
    e.preventDefault();
    if (sending || !threadId) return;
    setReplyError(null);
    if (!reply.trim()) {
      setReplyError("Please write a reply first.");
      return;
    }
    setSending(true);
    try {
      const res = await apiFetch<{ message: SupportMessage }>(`/support/${threadId}/reply`, {
        method: "POST",
        body: { body: reply.trim() }
      });
      setMessages((prev) => [...prev, res.message]);
      setReply("");
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setReplyError(err.message);
      else setReplyError("Could not send your reply. Please try again.");
    } finally {
      setSending(false);
    }
  }

  if (loading) {
    return (
      <AppShell isOwner={false}>
        <div className="flex flex-col items-center justify-center py-20">
          <div className="spinner" role="status" aria-label="Loading conversation" />
          <p className="muted mt-3 text-sm">Loading conversation…</p>
        </div>
      </AppShell>
    );
  }

  if (error || !thread) {
    return (
      <AppShell isOwner={user?.role === "owner"}>
        <Link href="/support" className="mt-2 inline-block text-sm text-purple-300">
          ← Back to support
        </Link>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error ?? "Could not load this conversation."}
        </p>
        <button type="button" onClick={load} className="primary-button mt-4">
          Try again
        </button>
      </AppShell>
    );
  }

  const isClosed = thread.status.toLowerCase() === "closed";

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <Link href="/support" className="mt-2 inline-block text-sm text-purple-300">
        ← Back to support
      </Link>
      <div className="mt-3 flex items-start justify-between gap-3">
        <h1 className="text-xl font-bold">{thread.subject}</h1>
        <span
          className={`badge ${
            isClosed ? "" : "border-green-500/50 bg-green-500/15 text-green-300"
          }`}
        >
          {thread.status.charAt(0).toUpperCase() + thread.status.slice(1)}
        </span>
      </div>
      <p className="muted mt-1 text-xs">
        Opened {fmtDateTime(thread.created_at)} · Updated {fmtDateTime(thread.updated_at)}
      </p>

      {messages.length === 0 ? (
        <div className="panel mt-5 p-6 text-center">
          <p className="muted text-sm">No messages in this thread yet.</p>
        </div>
      ) : (
        <ul className="mt-5 grid gap-3">
          {messages.map((m) => {
            const isOwner = m.author_role === "owner";
            return (
              <li
                key={m.id}
                className={`panel p-4 ${isOwner ? "border-purple-400/40" : ""}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <span
                    className={`badge ${isOwner ? "badge-purple" : ""}`}
                  >
                    {isOwner ? "Owner" : "You"}
                  </span>
                  <span className="muted text-xs">{fmtDateTime(m.created_at)}</span>
                </div>
                <p className="mt-2 whitespace-pre-wrap text-sm">{m.body}</p>
              </li>
            );
          })}
        </ul>
      )}

      {isClosed ? (
        <p className="mt-5 rounded-xl border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-sm text-muted">
          This thread is closed — replies are disabled. Start a new request if you need more help.
        </p>
      ) : (
        <form onSubmit={handleReply} className="panel mt-5 grid gap-3 p-4">
          <label className="grid gap-1.5 text-sm font-medium">
            Your reply
            <textarea
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              required
              rows={4}
              placeholder="Write your reply…"
            />
          </label>
          {replyError && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {replyError}
            </p>
          )}
          <button type="submit" className="primary-button w-full" disabled={sending}>
            {sending ? "Sending…" : "Send reply"}
          </button>
        </form>
      )}
    </AppShell>
  );
}
