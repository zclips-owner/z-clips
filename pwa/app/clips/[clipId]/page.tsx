"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { ErrorState, LoadingState } from "../_components/clip-ui";
import {
  formatDate,
  formatDuration,
  statusBadgeClass,
  statusLabel,
  type CaptionStyle,
  type ClipDetail,
  type ClipJob,
  type Entitlements,
  type UserSummary
} from "../_components/types";

interface ClipResponse {
  clip: ClipDetail;
  job: ClipJob;
  entitlements: Entitlements;
  caption_styles: CaptionStyle[];
  export_heights: number[];
  can_edit: boolean;
}

interface MeResponse {
  user: UserSummary;
  entitlements: Entitlements;
}

export default function ClipEditorPage() {
  const params = useParams();
  const clipId = params.clipId as string;
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [data, setData] = useState<ClipResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [startS, setStartS] = useState("");
  const [endS, setEndS] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const [exportHeight, setExportHeight] = useState("");
  const [facecamMode, setFacecamMode] = useState<"auto" | "manual" | "off">("auto");
  const [facecamX, setFacecamX] = useState("");
  const [facecamY, setFacecamY] = useState("");
  const [facecamW, setFacecamW] = useState("");
  const [facecamH, setFacecamH] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [me, res] = await Promise.all([
        apiFetch<MeResponse>("/me"),
        apiFetch<ClipResponse>(`/clips/${clipId}`)
      ]);
      setUser(me.user);
      setData(res);
      setStartS(res.clip.start_s !== null ? String(res.clip.start_s) : "0");
      setEndS(
        res.clip.end_s !== null
          ? String(res.clip.end_s)
          : res.clip.duration_s !== null
            ? String(res.clip.duration_s)
            : ""
      );
      setCaptionStyle(
        res.job.caption_style ?? res.caption_styles[0]?.key ?? "simple"
      );
      setExportHeight(
        String(res.job.export_height ?? res.export_heights[0] ?? 1280)
      );
      setFacecamMode(
        res.job.facecam_mode === "manual" || res.job.facecam_mode === "off"
          ? res.job.facecam_mode
          : "auto"
      );
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Could not load this clip.");
    } finally {
      setLoading(false);
    }
  }, [clipId, router]);

  useEffect(() => {
    load();
  }, [load]);

  const clip = data?.clip ?? null;
  const canEdit = data?.can_edit === true;
  // The contract returns can_edit but no reason field; derive the reason
  // from the clip status and the manual_editing entitlement.
  const editBlockedReason = !data
    ? null
    : clip?.status !== "candidate"
      ? `Only candidate clips can be edited (this clip is ${clip?.status}).`
      : !data.entitlements.manual_editing
        ? "Manual editing requires Lite/Pro — upgrade your plan to edit clips."
        : null;

  const showManualCoords =
    facecamMode === "manual" && !!data?.entitlements.advanced_positioning;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || !canEdit) return;
    setFormError(null);

    const start = Number(startS);
    const end = Number(endS);
    if (Number.isNaN(start) || Number.isNaN(end)) {
      setFormError("Enter start and end times in seconds.");
      return;
    }
    if (start < 0 || end < 0) {
      setFormError("Start and end times cannot be negative.");
      return;
    }
    if (start >= end) {
      setFormError("Start time must be less than end time.");
      return;
    }

    const body: Record<string, unknown> = {
      start_s: start,
      end_s: end,
      caption_style: captionStyle,
      export_height: Number(exportHeight),
      facecam_mode: facecamMode
    };
    if (showManualCoords) {
      const coords = { x: facecamX, y: facecamY, w: facecamW, h: facecamH };
      const parsed: Record<string, number> = {};
      for (const [key, value] of Object.entries(coords)) {
        if (value.trim() === "") continue;
        const n = Number(value);
        if (Number.isNaN(n)) {
          setFormError(`Facecam ${key} must be a number.`);
          return;
        }
        parsed[`facecam_${key}`] = n;
      }
      Object.assign(body, parsed);
    }

    setSubmitting(true);
    try {
      const res = await apiFetch<{ job: ClipJob }>(`/clips/${clipId}/edit`, {
        method: "POST",
        body
      });
      router.push(`/clips/jobs/${res.job.id}`);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      setFormError(
        err instanceof ApiError ? err.message : "Could not save the edit."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <div className="grid gap-4 pt-2">
        <Link href="/clips" className="text-sm font-semibold text-purple-300">
          ← Back to My Clips
        </Link>

        {loading && <LoadingState label="Loading clip…" />}
        {!loading && error && <ErrorState message={error} onRetry={load} />}

        {!loading && !error && data && clip && (
          <>
            <header className="flex items-start justify-between gap-2">
              <div>
                <h1 className="text-2xl font-bold">{clip.title}</h1>
                <p className="muted mt-1 text-sm">
                  {formatDate(clip.created_at)} · {formatDuration(clip.duration_s)}
                  {clip.width && clip.height ? ` · ${clip.width}×${clip.height}` : ""}
                </p>
              </div>
              <span className={statusBadgeClass(clip.status)}>
                {statusLabel(clip.status)}
              </span>
            </header>

            {clip.preview_url ? (
              <video
                src={clip.preview_url}
                controls
                playsInline
                preload="metadata"
                className="w-full rounded-2xl"
              />
            ) : (
              <div className="panel grid aspect-video place-items-center p-6 text-sm text-purple-300">
                No preview available for this clip.
              </div>
            )}

            {!canEdit && (
              <div
                role="alert"
                className="panel border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200"
              >
                <p className="font-semibold">Editing is not available</p>
                <p className="mt-1">
                  {editBlockedReason ??
                    "This clip cannot be edited right now."}
                </p>
              </div>
            )}

            {canEdit && (
              <form onSubmit={handleSubmit} className="panel grid gap-4 p-4">
                <p className="rounded-xl border border-purple-400/40 bg-purple-500/10 px-3 py-2 text-sm text-purple-200">
                  Saving this edit re-renders the clip and costs 1 credit.
                </p>

                <div className="grid grid-cols-2 gap-3">
                  <label className="grid gap-1.5 text-sm font-medium">
                    Trim start (seconds)
                    <input
                      type="number"
                      min={0}
                      step="0.1"
                      value={startS}
                      onChange={(e) => setStartS(e.target.value)}
                      required
                    />
                  </label>
                  <label className="grid gap-1.5 text-sm font-medium">
                    Trim end (seconds)
                    <input
                      type="number"
                      min={0}
                      step="0.1"
                      value={endS}
                      onChange={(e) => setEndS(e.target.value)}
                      required
                    />
                  </label>
                </div>
                <p className="muted -mt-2 text-xs">
                  Current clip: {clip.start_s ?? 0}s – {clip.end_s ?? clip.duration_s ?? "—"}s
                  {clip.duration_s !== null
                    ? ` (source length ${formatDuration(clip.duration_s)})`
                    : ""}
                </p>

                <label className="grid gap-1.5 text-sm font-medium">
                  Caption style
                  <select
                    value={captionStyle}
                    onChange={(e) => setCaptionStyle(e.target.value)}
                  >
                    {data.caption_styles.map((s) => (
                      <option key={s.key} value={s.key}>
                        {s.name}
                        {s.description ? ` — ${s.description}` : ""}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="grid gap-1.5 text-sm font-medium">
                  Export height
                  <select
                    value={exportHeight}
                    onChange={(e) => setExportHeight(e.target.value)}
                  >
                    {data.export_heights.map((h) => (
                      <option key={h} value={String(h)}>
                        {h}p{h === 2160 ? " (4K)" : ""}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="grid gap-1.5 text-sm font-medium">
                  Facecam mode
                  <select
                    value={facecamMode}
                    onChange={(e) =>
                      setFacecamMode(e.target.value as "auto" | "manual" | "off")
                    }
                  >
                    <option value="auto">Auto</option>
                    <option
                      value="manual"
                      disabled={!data.entitlements.advanced_positioning}
                    >
                      Manual
                      {!data.entitlements.advanced_positioning ? " (Pro only)" : ""}
                    </option>
                    <option value="off">Off</option>
                  </select>
                </label>

                {showManualCoords && (
                  <fieldset className="grid grid-cols-2 gap-3">
                    <legend className="text-sm font-medium">
                      Facecam position (optional)
                    </legend>
                    <label className="grid gap-1.5 text-sm">
                      X
                      <input
                        type="number"
                        step="0.01"
                        value={facecamX}
                        onChange={(e) => setFacecamX(e.target.value)}
                        placeholder="0"
                      />
                    </label>
                    <label className="grid gap-1.5 text-sm">
                      Y
                      <input
                        type="number"
                        step="0.01"
                        value={facecamY}
                        onChange={(e) => setFacecamY(e.target.value)}
                        placeholder="0"
                      />
                    </label>
                    <label className="grid gap-1.5 text-sm">
                      Width
                      <input
                        type="number"
                        step="0.01"
                        value={facecamW}
                        onChange={(e) => setFacecamW(e.target.value)}
                        placeholder="0"
                      />
                    </label>
                    <label className="grid gap-1.5 text-sm">
                      Height
                      <input
                        type="number"
                        step="0.01"
                        value={facecamH}
                        onChange={(e) => setFacecamH(e.target.value)}
                        placeholder="0"
                      />
                    </label>
                  </fieldset>
                )}

                {formError && (
                  <p
                    role="alert"
                    className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300"
                  >
                    {formError}
                  </p>
                )}

                <button type="submit" className="primary-button w-full" disabled={submitting}>
                  {submitting ? "Saving edit…" : "Save edit & re-render (1 credit)"}
                </button>
              </form>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
