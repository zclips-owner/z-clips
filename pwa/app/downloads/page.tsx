"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Download } from "lucide-react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { ClipCard, EmptyState, ErrorState, LoadingState } from "../clips/_components/clip-ui";
import type { ClipDetail, UserSummary } from "../clips/_components/types";

interface DownloadsResponse {
  clips: ClipDetail[];
}

interface MeResponse {
  user: UserSummary;
  entitlements: unknown;
}

export default function DownloadsPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [clips, setClips] = useState<ClipDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [quality, setQuality] = useState<"all" | "720" | "1080" | "2160">("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [me, res] = await Promise.all([
        apiFetch<MeResponse>("/me"),
        apiFetch<DownloadsResponse>("/downloads")
      ]);
      setUser(me.user);
      // The API returns approved clips only (enforced server-side).
      setClips(res.clips);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Could not load downloads.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return clips.filter((clip) => {
      if (q.length > 0 && !clip.title.toLowerCase().includes(q)) return false;
      if (quality !== "all") {
        const target = Number(quality);
        if (clip.height !== target) return false;
      }
      return true;
    });
  }, [clips, query, quality]);

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <div className="grid gap-4 pt-2">
        <header>
          <h1 className="text-2xl font-bold">Downloads</h1>
          <p className="muted mt-1 text-sm">
            Approved clips only — ready to download.
          </p>
        </header>

        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search approved clips…"
            aria-label="Search downloads"
          />
          <select
            value={quality}
            onChange={(e) =>
              setQuality(e.target.value as "all" | "720" | "1080" | "2160")
            }
            aria-label="Filter by quality"
            className="sm:w-44"
          >
            <option value="all">All qualities</option>
            <option value="720">720p</option>
            <option value="1080">1080p</option>
            <option value="2160">4K</option>
          </select>
        </div>

        {loading && <LoadingState label="Loading downloads…" />}
        {!loading && error && <ErrorState message={error} onRetry={load} />}

        {!loading && !error && clips.length === 0 && (
          <EmptyState message="No approved clips yet — approve a clip in My Clips first." />
        )}

        {!loading && !error && clips.length > 0 && filtered.length === 0 && (
          <EmptyState message="No approved clips match your search." />
        )}

        {!loading && !error && filtered.length > 0 && (
          <div className="grid gap-4">
            {filtered.map((clip) => (
              <ClipCard key={clip.id} clip={clip}>
                <a
                  href={`/api/v1/downloads/${clip.id}`}
                  download
                  className="primary-button flex items-center gap-1.5 !px-3 !py-2 text-sm"
                >
                  <Download size={15} /> Download
                </a>
              </ClipCard>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
