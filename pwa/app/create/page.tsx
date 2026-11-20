"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { ErrorState, LoadingState } from "../clips/_components/clip-ui";
import {
  DEFAULT_ASPECT_RATIO,
  DEFAULT_CAPTION_BG,
  DEFAULT_CAPTION_POSITION,
  DEFAULT_CAPTION_SIZE,
  DEFAULT_LAYOUT,
  FALLBACK_ASPECT_RATIOS,
  FALLBACK_CAPTION_BGS,
  FALLBACK_CAPTION_POSITIONS,
  FALLBACK_CAPTION_SIZES,
  FALLBACK_LAYOUTS,
  aspectRatioLabel,
  layoutLabel,
  optionList,
  type CaptionStyle,
  type EditorOptionLists,
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

interface SettingsResponse extends EditorOptionLists {
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
  const [aspectRatio, setAspectRatio] = useState(DEFAULT_ASPECT_RATIO);
  const [layout, setLayout] = useState(DEFAULT_LAYOUT);
  const [captionSize, setCaptionSize] = useState(DEFAULT_CAPTION_SIZE);
  const [captionPosition, setCaptionPosition] = useState(DEFAULT_CAPTION_POSITION);
  const [captionBg, setCaptionBg] = useState(DEFAULT_CAPTION_BG);
  const [stickerText, setStickerText] = useState("");
  const [postAuthor, setPostAuthor] = useState("");
  const [postText, setPostText] = useState("");
  const [aspectRatios, setAspectRatios] = useState<string[]>(FALLBACK_ASPECT_RATIOS);
  const [layouts, setLayouts] = useState<string[]>(FALLBACK_LAYOUTS);
  const [captionSizes, setCaptionSizes] = useState<string[]>(FALLBACK_CAPTION_SIZES);
  const [captionPositions, setCaptionPositions] = useState<string[]>(FALLBACK_CAPTION_POSITIONS);
  const [captionBgs, setCaptionBgs] = useState<string[]>(FALLBACK_CAPTION_BGS);
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

      // Option lists for the new editor fields: prefer the API lists,
      // fall back to the hardcoded values.
      setAspectRatios(optionList(settings.aspect_ratios, FALLBACK_ASPECT_RATIOS));
      setLayouts(optionList(settings.layouts, FALLBACK_LAYOUTS));
      setCaptionSizes(optionList(settings.caption_sizes, FALLBACK_CAPTION_SIZES));
      setCaptionPositions(
        optionList(settings.caption_positions, FALLBACK_CAPTION_POSITIONS)
      );
      setCaptionBgs(optionList(settings.caption_bgs, FALLBACK_CAPTION_BGS));
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
    form.append("aspect_ratio", aspectRatio);
    form.append("layout", layout);
    form.append("caption_size", captionSize);
    form.append("caption_position", captionPosition);
    form.append("caption_bg", captionBg);
    form.append("sticker_text", stickerText);
    form.append("post_author", postAuthor);
    form.append("post_text", postText);
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
                <option value="manual" disabled={!entitlements?.manual_editing}>
                  Manual{!entitlements?.manual_editing ? " (Lite/Pro)" : ""}
                </option>
                <option value="off">Off</option>
              </select>
              {!entitlements?.manual_editing && (
                <span className="muted text-xs">
                  Manual facecam positioning needs Lite or Pro.
                </span>
              )}
            </label>

            <label className="grid gap-1.5 text-sm font-medium">
              Aspect ratio
              <select
                value={aspectRatio}
                onChange={(e) => setAspectRatio(e.target.value)}
                disabled={outOfCredits}
              >
                {aspectRatios.map((r) => (
                  <option key={r} value={r}>
                    {aspectRatioLabel(r)}
                  </option>
                ))}
              </select>
            </label>

            <label className="grid gap-1.5 text-sm font-medium">
              Framing layout
              <select
                value={layout}
                onChange={(e) => setLayout(e.target.value)}
                disabled={outOfCredits}
              >
                {layouts.map((l) => (
                  <option key={l} value={l}>
                    {layoutLabel(l)}
                  </option>
                ))}
              </select>
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="grid gap-1.5 text-sm font-medium">
                Caption size
                <select
                  value={captionSize}
                  onChange={(e) => setCaptionSize(e.target.value)}
                  disabled={outOfCredits}
                >
                  {captionSizes.map((s) => (
                    <option key={s} value={s}>
                      {s.charAt(0).toUpperCase() + s.slice(1)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.5 text-sm font-medium">
                Caption position
                <select
                  value={captionPosition}
                  onChange={(e) => setCaptionPosition(e.target.value)}
                  disabled={outOfCredits}
                >
                  {captionPositions.map((p) => (
                    <option key={p} value={p}>
                      {p.charAt(0).toUpperCase() + p.slice(1)}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="grid gap-1.5 text-sm font-medium">
              Caption background
              <select
                value={captionBg}
                onChange={(e) => setCaptionBg(e.target.value)}
                disabled={outOfCredits}
              >
                {captionBgs.map((b) => (
                  <option key={b} value={b}>
                    {b.charAt(0).toUpperCase() + b.slice(1)}
                  </option>
                ))}
              </select>
            </label>

            <label className="grid gap-1.5 text-sm font-medium">
              Sticker / callout text
              <input
                type="text"
                value={stickerText}
                onChange={(e) => setStickerText(e.target.value)}
                maxLength={40}
                disabled={outOfCredits}
                placeholder="e.g. Wait for it…"
              />
              <span className="muted text-xs">
                {stickerText.length}/40 characters — leave empty for no
                sticker.
              </span>
            </label>

            <label className="grid gap-1.5 text-sm font-medium">
              Social post author
              <input
                type="text"
                value={postAuthor}
                onChange={(e) => setPostAuthor(e.target.value)}
                maxLength={40}
                disabled={outOfCredits}
                placeholder="e.g. @username"
              />
              <span className="muted text-xs">{postAuthor.length}/40 characters</span>
            </label>

            <label className="grid gap-1.5 text-sm font-medium">
              Social post text
              <input
                type="text"
                value={postText}
                onChange={(e) => setPostText(e.target.value)}
                maxLength={140}
                disabled={outOfCredits}
                placeholder="e.g. This moment broke the internet…"
              />
              <span className="muted text-xs">
                {postText.length}/140 characters — the social-post card only
                shows when both author and text are set.
              </span>
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
