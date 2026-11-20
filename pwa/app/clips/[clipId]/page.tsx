"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
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

/** Rectangle shown when the facecam X/Y/W/H fields are empty. */
const DEFAULT_FACECAM = { x: 0.68, y: 0.05, w: 0.28, h: 0.28 };

const clamp = (n: number, min: number, max: number) =>
  Math.min(max, Math.max(min, n));

/** Parse a 0–1 fraction field; fall back when the field is empty/invalid. */
const parseCoord = (value: string, fallback: number) => {
  if (value.trim() === "") return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

const round3 = (n: number) => String(Math.round(n * 1000) / 1000);

type DragState = {
  mode: "move" | "resize";
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startRect: typeof DEFAULT_FACECAM;
};

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

  // --- Draggable facecam rectangle (manual mode) -------------------------
  // The rectangle is derived from the X/Y/W/H fields (defaults when empty),
  // so typing numbers moves it and dragging it writes back into the fields.
  const previewRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);

  const facecamRect = {
    w: clamp(parseCoord(facecamW, DEFAULT_FACECAM.w), 0.05, 1),
    h: clamp(parseCoord(facecamH, DEFAULT_FACECAM.h), 0.05, 1),
    x: 0,
    y: 0
  };
  facecamRect.x = clamp(parseCoord(facecamX, DEFAULT_FACECAM.x), 0, 1 - facecamRect.w);
  facecamRect.y = clamp(parseCoord(facecamY, DEFAULT_FACECAM.y), 0, 1 - facecamRect.h);

  const applyRect = (r: typeof DEFAULT_FACECAM) => {
    setFacecamX(round3(r.x));
    setFacecamY(round3(r.y));
    setFacecamW(round3(r.w));
    setFacecamH(round3(r.h));
  };

  const startDrag = (e: React.PointerEvent, mode: "move" | "resize") => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = {
      mode,
      pointerId: e.pointerId,
      startClientX: e.clientX,
      startClientY: e.clientY,
      startRect: { ...facecamRect }
    };
  };

  const onDragMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    const box = previewRef.current;
    if (!drag || drag.pointerId !== e.pointerId || !box) return;
    const bounds = box.getBoundingClientRect();
    if (bounds.width === 0 || bounds.height === 0) return;
    const dx = (e.clientX - drag.startClientX) / bounds.width;
    const dy = (e.clientY - drag.startClientY) / bounds.height;
    const s = drag.startRect;
    if (drag.mode === "move") {
      applyRect({
        ...s,
        x: clamp(s.x + dx, 0, 1 - s.w),
        y: clamp(s.y + dy, 0, 1 - s.h)
      });
    } else {
      applyRect({
        ...s,
        w: clamp(s.w + dx, 0.05, 1 - s.x),
        h: clamp(s.h + dy, 0.05, 1 - s.y)
      });
    }
  };

  const endDrag = (e: React.PointerEvent) => {
    if (dragRef.current?.pointerId === e.pointerId) dragRef.current = null;
  };

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

            <div ref={previewRef} className="relative w-full">
              {clip.preview_url ? (
                <video
                  src={clip.preview_url}
                  controls
                  playsInline
                  preload="metadata"
                  className="block w-full rounded-2xl"
                />
              ) : (
                <div className="panel grid aspect-video place-items-center p-6 text-sm text-purple-300">
                  No preview available for this clip.
                </div>
              )}

              {showManualCoords && canEdit && (
                <div
                  role="button"
                  aria-label="Facecam position — drag to move, drag the corner handle to resize"
                  className="absolute cursor-move touch-none rounded-lg border-2 border-dashed border-orange-400 bg-orange-500/20 shadow-[0_0_0_1px_rgba(0,0,0,0.4)]"
                  style={{
                    left: `${facecamRect.x * 100}%`,
                    top: `${facecamRect.y * 100}%`,
                    width: `${facecamRect.w * 100}%`,
                    height: `${facecamRect.h * 100}%`,
                    touchAction: "none"
                  }}
                  onPointerDown={(e) => startDrag(e, "move")}
                  onPointerMove={onDragMove}
                  onPointerUp={endDrag}
                  onPointerCancel={endDrag}
                >
                  <span className="pointer-events-none absolute left-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-orange-300">
                    Facecam
                  </span>
                  <div
                    aria-label="Resize facecam"
                    className="absolute -bottom-2 -right-2 h-5 w-5 cursor-nwse-resize touch-none rounded-full border-2 border-white bg-orange-500"
                    style={{ touchAction: "none" }}
                    onPointerDown={(e) => startDrag(e, "resize")}
                    onPointerMove={onDragMove}
                    onPointerUp={endDrag}
                    onPointerCancel={endDrag}
                  />
                </div>
              )}
            </div>
            {showManualCoords && canEdit && (
              <p className="muted -mt-2 text-xs">
                Drag the orange box on the preview to move the facecam, or drag
                its corner handle to resize it. The X / Y / Width / Height
                fields below update as you drag.
              </p>
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
