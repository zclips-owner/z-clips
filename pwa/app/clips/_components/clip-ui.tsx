"use client";

import Link from "next/link";
import { useState } from "react";
import { Play } from "lucide-react";
import {
  formatDate,
  formatDuration,
  statusBadgeClass,
  statusLabel,
  type ClipDetail
} from "./types";

/** Shared loading spinner row. */
export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="muted text-sm">{label}</span>
    </div>
  );
}

/** Shared error box with a retry button. Server messages are shown verbatim. */
export function ErrorState({
  message,
  onRetry
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="panel mt-4 border-red-500/40 bg-red-500/10 p-4 text-sm text-red-200"
    >
      <p>{message}</p>
      <button type="button" onClick={onRetry} className="btn-secondary mt-3">
        Retry
      </button>
    </div>
  );
}

/** Shared empty state. */
export function EmptyState({ message }: { message: string }) {
  return (
    <div className="panel mt-4 p-6 text-center">
      <p className="muted text-sm">{message}</p>
    </div>
  );
}

/**
 * Clip card shared by My Clips and Downloads.
 * Thumbnail hides gracefully when thumb_url is null or the image fails.
 * Preview toggles an inline video player (preview_url). Extra actions
 * (approve/reject/edit/download) are passed in as children.
 */
export function ClipCard({
  clip,
  children
}: {
  clip: ClipDetail;
  children?: React.ReactNode;
}) {
  const [showPreview, setShowPreview] = useState(false);
  const [thumbBroken, setThumbBroken] = useState(false);

  return (
    <article className="panel card overflow-hidden">
      {clip.thumb_url && !thumbBroken ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={clip.thumb_url}
          alt={clip.title}
          className="aspect-video w-full object-cover"
          onError={() => setThumbBroken(true)}
        />
      ) : (
        <div className="grid aspect-video w-full place-items-center bg-purple-500/10 text-sm text-purple-300">
          No thumbnail
        </div>
      )}
      <div className="grid gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-semibold leading-snug">{clip.title}</h3>
          <span className={statusBadgeClass(clip.status)}>{statusLabel(clip.status)}</span>
        </div>
        <p className="muted text-xs">
          {formatDate(clip.created_at)} · {formatDuration(clip.duration_s)}
          {clip.width && clip.height ? ` · ${clip.width}×${clip.height}` : ""}
        </p>
        {clip.qc_passed !== null && clip.qc_passed !== undefined && (
          <p className="text-xs">
            QC:{" "}
            <span className={clip.qc_passed ? "text-green-300" : "text-red-300"}>
              {clip.qc_passed ? "Passed" : "Failed"}
            </span>
          </p>
        )}
        {showPreview && clip.preview_url && (
          <video
            src={clip.preview_url}
            controls
            playsInline
            preload="metadata"
            className="w-full rounded-xl"
          />
        )}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {clip.preview_url && (
            <button
              type="button"
              onClick={() => setShowPreview((v) => !v)}
              className="btn-secondary flex items-center gap-1.5 !px-3 !py-2 text-sm"
            >
              <Play size={15} /> {showPreview ? "Hide preview" : "Preview"}
            </button>
          )}
          <Link
            href={`/clips/${clip.id}`}
            className="btn-secondary !px-3 !py-2 text-sm"
          >
            Open
          </Link>
          {children}
        </div>
      </div>
    </article>
  );
}
