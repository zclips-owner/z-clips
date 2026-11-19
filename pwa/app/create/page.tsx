"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { ErrorState, LoadingState } from "../clips/_components/clip-ui";
import {
  type CaptionStyle,
  type Entitlements,
  type UserSummary
} from "../clips/_components/types";

interface MeResponse {
  user: UserSummary;
  entitlements: Entitlements;
}

interface DashboardResponse {
  credits: number;
  plan_name: string;
}

interface SettingsResponse {
  user: UserSummary;
  default_caption_style: string;
  default_export_height: number;
  caption_styles: CaptionStyle[];
  export_heights: number[];
}

interface CreateJobResponse {
  job: { id: number };
}

/**
 * Supported source hosts. NOTE: API_CONTRACT.md does not publish this list —
 * the server is the authority and rejects unsupported URLs with its own
 * message, which we show verbatim. These are the commonly supported sources.
 */
const SUPPORTED_HOSTS = [
  "YouTube",
  "TikTok",
  "Instagram",
  "Facebook",
  "X (Twitter)",
  "Direct video link (.mp4, .mov, .webm)"
];

export default function CreatePage() {
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [entitlements, setEntitlements] = useState<Entitlements | null>(null);
  const [credits, setCredits] = useState<number | null>(null);
  const [captionStyles, setCaptionStyles] = useState<CaptionStyle[]>([]);
  const [exportHeights, setExportHeights] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [sourceMode, setSourceMode] = useState<"upload" | "url">("upload");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const [exportHeight, setExportHeight] = useState("");
  const [facecamMode, setFacecamMode] = useState<"auto" | "manual" | "off">("auto");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [me, dash, settings] = await Promise.all([
        apiFetch<MeResponse>("/me"),
        apiFetch<DashboardResponse>("/dashboard"),
        apiFetch<SettingsResponse>("/settings")
      ]);
      setUser(me.user);
      setEntitlements(me.entitlements);
      setCredits(dash.credits);

      // Caption styles: the server's full list, restricted to the plan's
      // allowed caption_style_list when the entitlement provides one.
      const allowed = me.entitlements.caption_style_list;
      const styles =
        Array.isArray(allowed) && allowed.length > 0
          ? settings.caption_styles.filter((s) => allowed.includes(s.key))
          : settings.caption_styles;
      setCaptionStyles(styles);
      setCaptionStyle(
        styles.some((s) => s.key === settings.default_caption_style)
          ? settings.default_caption_style
          : styles[0]?.key ?? "simple"
      );

      // Export heights: drop 4K (2160) unless the plan allows 4K export.
      const heights = settings.export_heights.filter(
        (h) => h <= 1080 || me.entitlements["4k_export"]
      );
      setExportHeights(heights.length > 0 ? heights : settings.export_heights);
      setExportHeight(
        String(
          heights.includes(settings.default_export_height)
            ? settings.default_export_height
            : heights[0] ?? 1280
        )
      );
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Could not load Quick Create.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  const isUnlimited =
    user?.role === "owner" || user?.complimentary_premium === true;
  const outOfCredits = credits !== null && credits < 1 && !isUnlimited;

  function validateUrl(raw: string): string | null {
    let url: URL;
    try {
      url = new URL(raw.trim());
    } catch {
      return "Enter a valid URL starting with http:// or https://.";
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "Source URL must start with http:// or https://.";
    }
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || outOfCredits) return;
    setFormError(null);

    const cleanTitle = title.trim();
    if (cleanTitle.length === 0) {
      setFormError("Title is required.");
      return;
    }
    if (cleanTitle.length > 200) {
      setFormError("Title must be 200 characters or fewer.");
      return;
    }
    if (sourceMode === "upload" && !videoFile) {
      setFormError("Choose a video file to upload, or switch to a source URL.");
      return;
    }
    if (sourceMode === "url") {
      const urlError = validateUrl(sourceUrl);
      if (urlError) {
        setFormError(urlError);
        return;
      }
    }

    const form = new FormData();
    form.append("title", cleanTitle);
    form.append("caption_style", captionStyle || "simple");
    form.append("export_height", exportHeight || "1280");
    form.append("facecam_mode", facecamMode);
    if (sourceMode === "upload" && videoFile) {
      form.append("video", videoFile);
    } else if (sourceMode === "url") {
      form.append("source_url", sourceUrl.trim());
    }

    setSubmitting(true);
    try {
      const res = await apiFetch<CreateJobResponse>("/clips", {
        method: "POST",
        body: form
      });
      router.push(`/clips/jobs/${res.job.id}`);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      // Server validation errors are shown verbatim.
      setFormError(
        err instanceof ApiError ? err.message : "Could not create the clip job."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <div className="grid gap-4 pt-2">
        <header>
          <h1 className="text-2xl font-bold">Quick Create</h1>
          <p className="muted mt-1 text-sm">
            1 credit is deducted per clip job.{" "}
            {credits !== null && (
              <>
                You have <strong className="text-purple-300">{credits}</strong>{" "}
                credit{credits === 1 ? "" : "s"} left.
              </>
            )}
          </p>
        </header>

        {loading && <LoadingState label="Loading Quick Create…" />}
        {!loading && error && <ErrorState message={error} onRetry={load} />}

        {!loading && !error && outOfCredits && (
          <div
            role="alert"
            className="panel border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200"
          >
            <p className="font-semibold">No clip credits left</p>
            <p className="mt-1">
              Renew or upgrade your plan to create more clips.
            </p>
          </div>
        )}

        {!loading && !error && (
          <form onSubmit={handleSubmit} className="panel grid gap-4 p-4">
            <label className="grid gap-1.5 text-sm font-medium">
              Title <span className="text-red-300">*</span>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={200}
                required
                disabled={outOfCredits}
                placeholder="Name your clip"
              />
              <span className="muted text-xs">{title.length}/200 characters</span>
            </label>

            <fieldset className="grid gap-3">
              <legend className="text-sm font-medium">
                Source <span className="text-red-300">*</span>
              </legend>
              <div className="flex gap-2" role="group" aria-label="Source type">
                <button
                  type="button"
                  onClick={() => setSourceMode("upload")}
                  disabled={outOfCredits}
                  className={
                    sourceMode === "upload" ? "primary-button flex-1" : "btn-secondary flex-1"
                  }
                >
                  Upload video
                </button>
                <button
                  type="button"
                  onClick={() => setSourceMode("url")}
                  disabled={outOfCredits}
                  className={
                    sourceMode === "url" ? "primary-button flex-1" : "btn-secondary flex-1"
                  }
                >
                  Source URL
                </button>
              </div>
              {sourceMode === "upload" ? (
                <label className="grid gap-1.5 text-sm">
                  Video file
                  <input
                    type="file"
                    accept="video/*"
                    disabled={outOfCredits}
                    onChange={(e) => setVideoFile(e.target.files?.[0] ?? null)}
                  />
                  {videoFile && (
                    <span className="muted text-xs">
                      Selected: {videoFile.name} (
                      {(videoFile.size / 1024 / 1024).toFixed(1)} MB)
                    </span>
                  )}
                </label>
              ) : (
                <label className="grid gap-1.5 text-sm">
                  Source URL
                  <input
                    type="url"
                    value={sourceUrl}
                    onChange={(e) => setSourceUrl(e.target.value)}
                    disabled={outOfCredits}
                    placeholder="https://…"
                    inputMode="url"
                  />
                  <span className="muted text-xs">
                    Supported sources: {SUPPORTED_HOSTS.join(", ")}. The server
                    checks the URL and reports any problem.
                  </span>
                </label>
              )}
            </fieldset>

            <label className="grid gap-1.5 text-sm font-medium">
              Caption style
              <select
                value={captionStyle}
                onChange={(e) => setCaptionStyle(e.target.value)}
                disabled={outOfCredits}
              >
                {captionStyles.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.name}
                    {s.description ? ` — ${s.description}` : ""}
                  </option>
                ))}
              </select>
              {captionStyles.length === 0 && (
                <span className="muted text-xs">
                  No caption styles are available on your plan.
                </span>
              )}
            </label>

            <label className="grid gap-1.5 text-sm font-medium">
              Export height
              <select
                value={exportHeight}
                onChange={(e) => setExportHeight(e.target.value)}
                disabled={outOfCredits}
              >
                {exportHeights.map((h) => (
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
                disabled={outOfCredits}
              >
                <option value="auto">Auto</option>
                <option value="manual" disabled={!entitlements?.advanced_positioning}>
                  Manual{!entitlements?.advanced_positioning ? " (Pro only)" : ""}
                </option>
                <option value="off">Off</option>
              </select>
              {!entitlements?.advanced_positioning && (
                <span className="muted text-xs">
                  Manual facecam positioning needs the Pro plan.
                </span>
              )}
            </label>

            {formError && (
              <p
                role="alert"
                className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300"
              >
                {formError}
              </p>
            )}

            <button
              type="submit"
              className="primary-button w-full"
              disabled={submitting || outOfCredits}
            >
              {submitting ? "Creating clip job…" : "Create clip (1 credit)"}
            </button>
          </form>
        )}
      </div>
    </AppShell>
  );
}
