"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { ClipCard, EmptyState, ErrorState, LoadingState } from "./_components/clip-ui";
import { formatDateTime, type ClipDetail, type ClipJob, type UserSummary } from "./_components/types";

type StatusFilter = "all" | "candidate" | "approved" | "rejected";

interface ClipsResponse {
  clips: ClipDetail[];
  jobs: ClipJob[];
}

interface MeResponse {
  user: UserSummary;
  entitlements: unknown;
}

const ACTIVE_JOB_STATUSES = new Set([
  "queued",
  "analyzing",
  "rendering",
  "qc",
  "needs_model"
]);

export default function ClipsPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [clips, setClips] = useState<ClipDetail[]>([]);
  const [jobs, setJobs] = useState<ClipJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [busyClipId, setBusyClipId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [me, res] = await Promise.all([
        apiFetch<MeResponse>("/me"),
        apiFetch<ClipsResponse>(
          `/clips?q=${encodeURIComponent(query)}${status !== "all" ? `&status=${status}` : ""}`
        )
      ]);
      setUser(me.user);
      setClips(res.clips);
      setJobs(res.jobs);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Could not load your clips.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, status]);

  useEffect(() => {
    load();
  }, [load]);

  // Debounced title search; status changes reload immediately via load().
  // Skipped on first render — the mount effect above already loads.
  const firstQueryRun = useRef(true);
  useEffect(() => {
    if (firstQueryRun.current) {
      firstQueryRun.current = false;
      return;
    }
    const t = setTimeout(() => {
      load();
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  async function decide(clipId: number, action: "approve" | "reject") {
    setBusyClipId(clipId);
    setActionError(null);
    try {
      await apiFetch(`/clips/${clipId}/${action}`, { method: "POST" });
      await load();
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      setActionError(
        err instanceof ApiError ? err.message : `Could not ${action} the clip.`
      );
    } finally {
      setBusyClipId(null);
    }
  }

  const activeJobs = jobs.filter((job) => ACTIVE_JOB_STATUSES.has(job.status));

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <div className="grid gap-4 pt-2">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">My Clips</h1>
          <Link href="/create" className="primary-button !px-4 !py-2 text-sm">
            + New clip
          </Link>
        </header>

        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search clips by title…"
            aria-label="Search clips"
          />
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
            aria-label="Filter by status"
            className="sm:w-44"
          >
            <option value="all">All statuses</option>
            <option value="candidate">Candidate</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>

        {actionError && (
          <p
            role="alert"
            className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300"
          >
            {actionError}
          </p>
        )}

        {loading && <LoadingState label="Loading clips…" />}
        {!loading && error && <ErrorState message={error} onRetry={load} />}

        {!loading && !error && (
          <>
            {activeJobs.length > 0 && (
              <section aria-label="Clips in progress">
                <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-purple-200">
                  In progress
                </h2>
                <ul className="grid gap-2">
                  {activeJobs.map((job) => (
                    <li key={job.id}>
                      <Link
                        href={`/clips/jobs/${job.id}`}
                        className="panel card flex items-center justify-between gap-3 p-4"
                      >
                        <span>
                          <span className="block text-sm font-semibold">{job.title}</span>
                          <span className="muted text-xs">
                            {job.status}
                            {job.progress !== null && job.progress !== undefined
                              ? ` · ${job.progress}%`
                              : ""}{" "}
                            · {formatDateTime(job.updated_at)}
                          </span>
                        </span>
                        <span className="text-sm font-semibold text-purple-300">View →</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {clips.length === 0 ? (
              <EmptyState message="No clips yet — create your first clip from Quick Create." />
            ) : (
              <div className="grid gap-4">
                {clips.map((clip) => (
                  <ClipCard key={clip.id} clip={clip}>
                    {clip.status === "candidate" && (
                      <>
                        <button
                          type="button"
                          onClick={() => decide(clip.id, "approve")}
                          disabled={busyClipId === clip.id}
                          className="primary-button !px-3 !py-2 text-sm"
                        >
                          {busyClipId === clip.id ? "Working…" : "Approve"}
                        </button>
                        <button
                          type="button"
                          onClick={() => decide(clip.id, "reject")}
                          disabled={busyClipId === clip.id}
                          className="btn-danger !px-3 !py-2 text-sm"
                        >
                          Reject
                        </button>
                        <Link
                          href={`/clips/${clip.id}`}
                          className="btn-secondary !px-3 !py-2 text-sm"
                        >
                          Edit
                        </Link>
                      </>
                    )}
                  </ClipCard>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
