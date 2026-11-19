"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { ClipCard, EmptyState, ErrorState, LoadingState } from "../../_components/clip-ui";
import {
  formatDateTime,
  jobLogLines,
  type ClipDetail,
  type ClipJob,
  type SourceVideo,
  type UserSummary
} from "../../_components/types";

interface JobResponse {
  job: ClipJob;
  clips: ClipDetail[];
  source: SourceVideo;
}

interface MeResponse {
  user: UserSummary;
  entitlements: unknown;
}

const ACTIVE_STATUSES = new Set(["queued", "analyzing", "rendering", "qc"]);

export default function JobPage() {
  const params = useParams();
  const jobId = params.jobId as string;
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [data, setData] = useState<JobResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [me, res] = await Promise.all([
        apiFetch<MeResponse>("/me"),
        apiFetch<JobResponse>(`/clips/jobs/${jobId}`)
      ]);
      setUser(me.user);
      setData(res);
      setError(null);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Could not load this job.");
    } finally {
      setLoading(false);
    }
  }, [jobId, router]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll every 3s only while the job is still running.
  const isActive = data ? ACTIVE_STATUSES.has(data.job.status) : false;
  useEffect(() => {
    if (!isActive) return;
    const t = setInterval(() => {
      load();
    }, 3000);
    return () => clearInterval(t);
  }, [isActive, load]);

  const job = data?.job ?? null;
  const progress =
    job?.progress !== null && job?.progress !== undefined
      ? Math.max(0, Math.min(100, job.progress))
      : null;
  const logLines = job ? jobLogLines(job.log) : [];

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <div className="grid gap-4 pt-2">
        <Link href="/clips" className="text-sm font-semibold text-purple-300">
          ← Back to My Clips
        </Link>

        {loading && <LoadingState label="Loading job…" />}
        {!loading && error && !data && <ErrorState message={error} onRetry={load} />}
        {!loading && data && job && (
          <>
            <header>
              <h1 className="text-2xl font-bold">{job.title}</h1>
              <p className="muted mt-1 text-sm">
                Job #{job.id} · started {formatDateTime(job.created_at)}
              </p>
            </header>

            <div className="panel p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="badge badge-purple">{job.status}</span>
                {progress !== null && (
                  <span className="text-sm font-semibold">{progress}%</span>
                )}
              </div>
              <div
                className="mt-3 h-2.5 w-full overflow-hidden rounded-full bg-purple-500/15"
                role="progressbar"
                aria-valuenow={progress ?? 0}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className="h-full rounded-full bg-purple-500 transition-all"
                  style={{ width: `${progress ?? 0}%` }}
                />
              </div>
              <p className="muted mt-2 text-xs">
                {isActive
                  ? "Still processing — this page refreshes automatically."
                  : job.status === "done"
                    ? "Finished."
                    : job.status === "failed"
                      ? "This job failed."
                      : job.status === "needs_model"
                        ? "Waiting on the owner to install a required model."
                        : `Status: ${job.status}`}
              </p>
            </div>

            {job.status === "needs_model" && (
              <div
                role="alert"
                className="panel border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200"
              >
                <p className="font-semibold">Model not installed</p>
                <p className="mt-1">
                  faster-whisper is not installed on the server, so this job cannot
                  continue. The owner must install it first (Owner → System &amp;
                  Models). No progress is being made on this job.
                </p>
              </div>
            )}

            {job.status === "failed" && job.error && (
              <div
                role="alert"
                className="panel border-red-500/40 bg-red-500/10 p-4 text-sm text-red-200"
              >
                <p className="font-semibold">Job failed</p>
                <p className="mt-1">{job.error}</p>
              </div>
            )}

            {logLines.length > 0 && (
              <section aria-label="Job log">
                <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-purple-200">
                  Log
                </h2>
                <pre className="panel max-h-64 overflow-y-auto whitespace-pre-wrap p-4 font-mono text-xs leading-relaxed">
                  {logLines.join("\n")}
                </pre>
              </section>
            )}

            {data.source && (
              <p className="muted text-xs">
                Source: {data.source.title}
                {data.source.original_filename ? ` (${data.source.original_filename})` : ""}
                {data.source.source_type ? ` · ${data.source.source_type}` : ""}
              </p>
            )}

            {job.status === "done" && (
              <section aria-label="Resulting clips">
                <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-purple-200">
                  Resulting clips
                </h2>
                {data.clips.length === 0 ? (
                  <EmptyState message="Job finished but produced no clips." />
                ) : (
                  <div className="grid gap-4">
                    {data.clips.map((clip) => (
                      <ClipCard key={clip.id} clip={clip}>
                        <Link
                          href={`/clips/${clip.id}`}
                          className="btn-secondary !px-3 !py-2 text-sm"
                        >
                          Review &amp; approve
                        </Link>
                      </ClipCard>
                    ))}
                  </div>
                )}
              </section>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
