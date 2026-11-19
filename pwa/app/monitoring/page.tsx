"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/**
 * YouTube Monitoring (Pro-only).
 * GET /api/v1/yt → { configured, youtube_monitoring, channels }.
 * POST /api/v1/yt/channels, .../check, .../delete, .../authorize.
 */

interface UserSummary {
  id: number;
  username: string;
  role: string;
  plan: string;
  effective_plan: string;
}

interface YTChannel {
  id: number;
  channel_url: string;
  channel_handle: string | null;
  channel_id: string | null;
  display_name: string | null;
  is_live: boolean;
  last_status: string | null;
  last_checked_at: string | null;
  live_video_id: string | null;
  live_title: string | null;
  ingestion_state: string | null;
  authorized_source_note: string | null;
  created_at: string;
}

interface YtResponse {
  configured: boolean;
  youtube_monitoring: boolean;
  channels: YTChannel[];
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "Not checked yet";
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

export default function MonitoringPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [data, setData] = useState<YtResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** True when the server says monitoring is locked for this plan (403 or flag). */
  const [planLocked, setPlanLocked] = useState(false);

  const [channelUrl, setChannelUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [busyChannel, setBusyChannel] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Per-channel authorize note inputs, keyed by channel id.
  const [authNotes, setAuthNotes] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPlanLocked(false);
    try {
      const me = await apiFetch<{ user: UserSummary }>("/me");
      setUser(me.user);
      const yt = await apiFetch<YtResponse>("/yt");
      setData(yt);
      if (!yt.youtube_monitoring) setPlanLocked(true);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) {
        if (err.status === 403) {
          // Pro gate enforced server-side: show the upgrade state, not a crash.
          setPlanLocked(true);
          setData({ configured: false, youtube_monitoring: false, channels: [] });
        } else {
          setError(err.message);
        }
      } else {
        setError("Could not load YouTube monitoring. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (adding) return;
    setFormError(null);
    if (!channelUrl.trim()) {
      setFormError("Please paste a YouTube channel URL.");
      return;
    }
    setAdding(true);
    try {
      const res = await apiFetch<{ channel: YTChannel }>("/yt/channels", {
        method: "POST",
        body: { channel_url: channelUrl.trim() }
      });
      setData((prev) =>
        prev ? { ...prev, channels: [res.channel, ...prev.channels] } : prev
      );
      setChannelUrl("");
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) {
        if (err.status === 403) setPlanLocked(true);
        setFormError(err.message);
      } else {
        setFormError("Could not add this channel. Please try again.");
      }
    } finally {
      setAdding(false);
    }
  }

  async function channelAction(
    channelId: number,
    action: "check" | "delete" | "authorize",
    note?: string
  ) {
    if (busyChannel !== null) return;
    if (action === "delete" && !window.confirm("Remove this channel from monitoring?")) return;
    setActionError(null);
    setBusyChannel(channelId);
    try {
      if (action === "delete") {
        await apiFetch(`/yt/channels/${channelId}/delete`, { method: "POST" });
        setData((prev) =>
          prev ? { ...prev, channels: prev.channels.filter((c) => c.id !== channelId) } : prev
        );
      } else if (action === "check") {
        const res = await apiFetch<{ channel: YTChannel }>(`/yt/channels/${channelId}/check`, {
          method: "POST"
        });
        setData((prev) =>
          prev
            ? {
                ...prev,
                channels: prev.channels.map((c) => (c.id === channelId ? res.channel : c))
              }
            : prev
        );
      } else {
        const res = await apiFetch<{ channel: YTChannel }>(
          `/yt/channels/${channelId}/authorize`,
          { method: "POST", body: { note: note ?? "" } }
        );
        setData((prev) =>
          prev
            ? {
                ...prev,
                channels: prev.channels.map((c) => (c.id === channelId ? res.channel : c))
              }
            : prev
        );
      }
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setActionError(err.message);
      else setActionError("That action failed. Please try again.");
    } finally {
      setBusyChannel(null);
    }
  }

  if (loading) {
    return (
      <AppShell isOwner={false}>
        <div className="flex flex-col items-center justify-center py-20">
          <div className="spinner" role="status" aria-label="Loading YouTube monitoring" />
          <p className="muted mt-3 text-sm">Loading YouTube monitoring…</p>
        </div>
      </AppShell>
    );
  }

  if (error && !data) {
    return (
      <AppShell isOwner={user?.role === "owner"}>
        <h1 className="pt-2 text-xl font-bold">YouTube Monitoring</h1>
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
      <h1 className="pt-2 text-xl font-bold">YouTube Monitoring</h1>
      <p className="muted mt-1 text-sm">
        Watch your channels for live streams and get notified when they go live.
      </p>

      {planLocked || (data && !data.youtube_monitoring) ? (
        <div className="panel mt-5 p-6 text-center">
          <p className="text-lg font-bold">YouTube Monitoring requires the Pro plan</p>
          <p className="muted mt-2 text-sm">
            Live channel monitoring is a Pro feature. Upgrade to Pro to add channels and check
            live status.
          </p>
          <Link href="/pay?plan=pro" className="primary-button mt-5 block w-full text-center">
            Upgrade to Pro
          </Link>
          <Link href="/plans" className="btn-secondary mt-3 block w-full text-center">
            View plans
          </Link>
        </div>
      ) : data && !data.configured ? (
        <div className="panel mt-5 p-6">
          <p className="font-bold">YouTube API key not configured</p>
          <p className="muted mt-2 text-sm">
            YouTube API key not configured — the owner must add a YouTube Data API v3 key before
            channels can be monitored. No live data is available yet.
          </p>
          <button type="button" onClick={load} className="btn-secondary mt-4">
            Check again
          </button>
        </div>
      ) : (
        data && (
          <>
            <form onSubmit={handleAdd} className="panel mt-5 grid gap-3 p-5">
              <h2 className="font-bold">Add a channel</h2>
              <label className="grid gap-1.5 text-sm font-medium">
                Channel URL
                <input
                  type="url"
                  value={channelUrl}
                  onChange={(e) => setChannelUrl(e.target.value)}
                  required
                  placeholder="https://www.youtube.com/@channelname"
                />
              </label>
              {formError && (
                <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                  {formError}
                </p>
              )}
              <button type="submit" className="primary-button w-full" disabled={adding}>
                {adding ? "Adding…" : "Add channel"}
              </button>
              <p className="muted text-xs">
                Only add channels you own or are authorised to monitor. A public URL does not
                grant downloading rights.
              </p>
            </form>

            {actionError && (
              <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {actionError}
              </p>
            )}

            {data.channels.length === 0 ? (
              <div className="panel mt-5 p-6 text-center">
                <p className="font-semibold">No channels yet</p>
                <p className="muted mt-1 text-sm">
                  Add a YouTube channel URL above to start monitoring it.
                </p>
              </div>
            ) : (
              <ul className="mt-5 grid gap-3">
                {data.channels.map((ch) => (
                  <li key={ch.id} className="panel p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate font-bold">
                          {ch.display_name || ch.channel_handle || ch.channel_id || "Channel"}
                        </p>
                        <p className="muted truncate text-xs">{ch.channel_url}</p>
                        {ch.channel_handle && (
                          <p className="muted text-xs">{ch.channel_handle}</p>
                        )}
                      </div>
                      <span
                        className={`badge shrink-0 ${
                          ch.is_live
                            ? "border-red-500/50 bg-red-500/15 text-red-300"
                            : "border-green-500/50 bg-green-500/15 text-green-300"
                        }`}
                      >
                        {ch.is_live ? "● Live" : ch.last_checked_at ? "Offline" : "Not checked"}
                      </span>
                    </div>

                    {ch.is_live && ch.live_title && (
                      <p className="mt-2 text-sm">
                        <span className="muted">Now live: </span>
                        {ch.live_title}
                      </p>
                    )}
                    <p className="muted mt-2 text-xs">
                      Last checked: {fmtDateTime(ch.last_checked_at)}
                      {ch.last_status ? ` · ${ch.last_status}` : ""}
                    </p>
                    {ch.authorized_source_note && (
                      <p className="mt-1 text-xs text-purple-200">
                        Authorised source: {ch.authorized_source_note}
                      </p>
                    )}

                    <div className="mt-4 grid gap-2">
                      <label className="grid gap-1.5 text-xs font-medium">
                        Authorised source note
                        <input
                          type="text"
                          value={authNotes[ch.id] ?? ""}
                          onChange={(e) =>
                            setAuthNotes((prev) => ({ ...prev, [ch.id]: e.target.value }))
                          }
                          placeholder="e.g. I own this channel / authorised by the owner"
                        />
                      </label>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => channelAction(ch.id, "check")}
                          disabled={busyChannel === ch.id}
                          className="btn-secondary flex-1 px-2 py-2 text-sm"
                        >
                          {busyChannel === ch.id ? "Working…" : "Check"}
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            channelAction(ch.id, "authorize", authNotes[ch.id] ?? "")
                          }
                          disabled={busyChannel === ch.id}
                          className="btn-secondary flex-1 px-2 py-2 text-sm"
                        >
                          Authorize
                        </button>
                        <button
                          type="button"
                          onClick={() => channelAction(ch.id, "delete")}
                          disabled={busyChannel === ch.id}
                          className="btn-danger flex-1 px-2 py-2 text-sm"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )
      )}
    </AppShell>
  );
}
