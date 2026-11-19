"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/** GET /api/v1/settings + POST /api/v1/settings */

interface UserSummary {
  id: number;
  username: string;
  email: string;
  display_name: string;
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

interface CaptionStyle {
  key: string;
  name: string;
  description: string;
}

interface SettingsResponse {
  user: UserSummary;
  default_caption_style: string;
  default_export_height: number;
  caption_styles: CaptionStyle[];
  export_heights: number[];
}

export default function SettingsPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [captionStyles, setCaptionStyles] = useState<CaptionStyle[]>([]);
  const [exportHeights, setExportHeights] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [captionStyle, setCaptionStyle] = useState("");
  const [exportHeight, setExportHeight] = useState<number>(1280);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await apiFetch<{ user: UserSummary }>("/me");
      setUser(me.user);
      const res = await apiFetch<SettingsResponse>("/settings");
      setUser(res.user);
      setCaptionStyles(res.caption_styles ?? []);
      setExportHeights(res.export_heights ?? []);
      setDisplayName(res.user.display_name ?? "");
      setEmail(res.user.email ?? "");
      setCaptionStyle(res.default_caption_style ?? "");
      setExportHeight(res.default_export_height ?? 1280);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setError(err.message);
      else setError("Could not load your settings. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (saving) return;
    setSaveError(null);
    setSaveSuccess(false);
    if (displayName.length > 80) {
      setSaveError("Display name must be 80 characters or fewer.");
      return;
    }
    if (!email.trim()) {
      setSaveError("Please enter an email address.");
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch<{ user: UserSummary }>("/settings", {
        method: "POST",
        body: {
          display_name: displayName,
          email: email.trim(),
          default_caption_style: captionStyle,
          default_export_height: exportHeight
        }
      });
      setUser(res.user);
      setSaveSuccess(true);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setSaveError(err.message);
      else setSaveError("Could not save your settings. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <AppShell isOwner={false}>
        <div className="flex flex-col items-center justify-center py-20">
          <div className="spinner" role="status" aria-label="Loading settings" />
          <p className="muted mt-3 text-sm">Loading settings…</p>
        </div>
      </AppShell>
    );
  }

  if (error || !user) {
    return (
      <AppShell isOwner={user?.role === "owner"}>
        <h1 className="pt-2 text-xl font-bold">Settings</h1>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error ?? "Could not load your settings."}
        </p>
        <button type="button" onClick={load} className="primary-button mt-4">
          Try again
        </button>
      </AppShell>
    );
  }

  return (
    <AppShell isOwner={user.role === "owner"}>
      <h1 className="pt-2 text-xl font-bold">Settings</h1>
      <p className="muted mt-1 text-sm">Your profile and clip defaults.</p>

      <section className="panel mt-5 p-5" aria-label="Account">
        <h2 className="font-bold">Account</h2>
        <dl className="mt-3 grid gap-2 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="muted">Username</dt>
            <dd className="font-semibold">{user.username}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="muted">Role</dt>
            <dd className="font-semibold capitalize">{user.role}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="muted">Plan</dt>
            <dd className="font-semibold capitalize">{user.effective_plan ?? user.plan}</dd>
          </div>
        </dl>
      </section>

      <form onSubmit={handleSave} className="panel mt-5 grid gap-4 p-5">
        <h2 className="font-bold">Profile &amp; defaults</h2>
        <label className="grid gap-1.5 text-sm font-medium">
          Display name (max 80 characters)
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            maxLength={80}
            placeholder="Your display name"
          />
        </label>
        <label className="grid gap-1.5 text-sm font-medium">
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            placeholder="you@example.com"
          />
        </label>
        <label className="grid gap-1.5 text-sm font-medium">
          Default caption style
          <select
            value={captionStyle}
            onChange={(e) => setCaptionStyle(e.target.value)}
            disabled={captionStyles.length === 0}
          >
            {captionStyles.length === 0 && <option value="">No styles available</option>}
            {captionStyles.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name}
                {s.description ? ` — ${s.description}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1.5 text-sm font-medium">
          Default export height
          <select
            value={exportHeight}
            onChange={(e) => setExportHeight(Number(e.target.value))}
            disabled={exportHeights.length === 0}
          >
            {exportHeights.length === 0 && <option value={1280}>1280</option>}
            {exportHeights.map((h) => (
              <option key={h} value={h}>
                {h}p
              </option>
            ))}
          </select>
        </label>
        {saveError && (
          <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {saveError}
          </p>
        )}
        {saveSuccess && (
          <p role="status" className="rounded-xl border border-green-500/40 bg-green-500/10 px-3 py-2 text-sm text-green-300">
            Settings saved.
          </p>
        )}
        <button type="submit" className="primary-button w-full" disabled={saving}>
          {saving ? "Saving…" : "Save settings"}
        </button>
      </form>
    </AppShell>
  );
}
