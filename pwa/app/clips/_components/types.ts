/**
 * Shared contract types + small UI helpers for the customer pages.
 * Types mirror ~/workspace/z-clips/API_CONTRACT.md exactly; fields the
 * contract leaves untyped are kept loose and handled defensively.
 */

export interface UserSummary {
  id: number;
  username: string;
  email: string;
  display_name: string | null;
  role: string;
  plan: string;
  effective_plan: string;
  credits: number;
  clips_used: number;
  plan_started_at: string | null;
  plan_ends_at: string | null;
  is_active: boolean;
  is_suspended: boolean;
  complimentary_premium: boolean;
  created_at: string;
}

export interface Entitlements {
  name: string;
  monthly_clips: number;
  manual_editing: boolean;
  /** Count or list, depending on the server build — treated as opaque. */
  caption_styles: unknown;
  caption_style_list: string[];
  youtube_live_monitoring: boolean;
  "4k_export": boolean;
  advanced_positioning: boolean;
  payment_required: boolean;
}

export interface Subscription {
  id: number;
  plan: string;
  started_at: string | null;
  ends_at: string | null;
  credits_granted: number;
  status: string;
  is_promotional: boolean;
  promo_price_pkr: number | null;
}

export interface Promotion {
  name: string;
  discount_pct: number;
  promo_price_pkr: number;
  normal_price_pkr: number;
  max_seats: number;
  seats_reserved: number;
  seats_used: number;
  seats_remaining: number;
  ends_at: string | null;
  is_active: boolean;
}

export interface Activity {
  id: number;
  kind: string;
  label: string;
  detail: string | null;
  created_at: string;
}

export interface ClipDetail {
  id: number;
  job_id: number | null;
  source_id: number | null;
  title: string;
  start_s: number | null;
  end_s: number | null;
  duration_s: number | null;
  score: number | null;
  hook_score: number | null;
  width: number | null;
  height: number | null;
  status: string;
  qc_passed: boolean | null;
  downloads: number;
  created_at: string;
  approved_at: string | null;
  thumb_url: string | null;
  preview_url: string | null;
}

export interface ClipJob {
  id: number;
  title: string;
  status: string;
  progress: number | null;
  /** Contract does not pin the shape; the server may send lines or text. */
  log: string[] | string | null;
  error: string | null;
  caption_style: string | null;
  export_height: number | null;
  trim_start_s: number | null;
  trim_end_s: number | null;
  facecam_mode: string | null;
  attempts: number;
  created_at: string;
  updated_at: string;
}

export interface CaptionStyle {
  key: string;
  name: string;
  description: string;
}

export interface SourceVideo {
  id: number;
  title: string;
  source_type: string;
  original_filename: string | null;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  has_audio: boolean;
  file_size: number | null;
  status: string;
  error: string | null;
  created_at: string;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric"
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

/** Normalise a job log that may arrive as lines or as one block of text. */
export function jobLogLines(log: ClipJob["log"]): string[] {
  if (!log) return [];
  if (Array.isArray(log)) return log;
  return log.split(/\r?\n/).filter((line) => line.trim().length > 0);
}

export function statusBadgeClass(status: string): string {
  switch (status) {
    case "approved":
    case "done":
      return "badge badge-purple";
    case "candidate":
      return "badge";
    case "rejected":
    case "failed":
      return "badge";
    default:
      return "badge";
  }
}

export function statusLabel(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}
