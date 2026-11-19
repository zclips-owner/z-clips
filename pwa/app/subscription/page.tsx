"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/** GET /api/v1/subscription */

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

interface Entitlements {
  name: string;
  monthly_clips: number;
  manual_editing: boolean;
  caption_styles: number | string;
  caption_style_list: string[];
  youtube_live_monitoring: boolean;
  "4k_export": boolean;
  advanced_positioning: boolean;
  payment_required: boolean;
}

interface Subscription {
  id: number;
  plan: string;
  started_at: string | null;
  ends_at: string | null;
  credits_granted: number;
  status: string;
  is_promotional: boolean;
  promo_price_pkr: number | null;
}

interface CreditLedger {
  id: number;
  delta: number;
  reason: string;
  ref_type: string | null;
  ref_id: number | null;
  created_at: string;
}

interface Promotion {
  name: string;
  discount_pct: number;
  promo_price_pkr: number;
  normal_price_pkr: number;
  max_seats: number;
  seats_reserved: number;
  seats_used: number;
  seats_remaining: number;
  ends_at: string;
  is_active: boolean;
}

interface SubscriptionResponse {
  user: UserSummary;
  entitlements: Entitlements;
  subscription: Subscription | null;
  history: Subscription[];
  ledger: CreditLedger[];
  promotion: Promotion | null;
  promo_active: boolean;
  plan_change_note: string;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export default function SubscriptionPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [data, setData] = useState<SubscriptionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await apiFetch<{ user: UserSummary }>("/me");
      setUser(me.user);
      const sub = await apiFetch<SubscriptionResponse>("/subscription");
      setData(sub);
      if (sub.user) setUser(sub.user);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setError(err.message);
      else setError("Could not load your subscription. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <AppShell isOwner={false}>
        <div className="flex flex-col items-center justify-center py-20">
          <div className="spinner" role="status" aria-label="Loading subscription" />
          <p className="muted mt-3 text-sm">Loading your subscription…</p>
        </div>
      </AppShell>
    );
  }

  if (error || !data) {
    return (
      <AppShell isOwner={user?.role === "owner"}>
        <h1 className="pt-2 text-xl font-bold">Subscription</h1>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error ?? "Could not load your subscription."}
        </p>
        <button type="button" onClick={load} className="primary-button mt-4">
          Try again
        </button>
      </AppShell>
    );
  }

  const promo = data.promotion;
  const ent = data.entitlements;
  const sub = data.subscription;
  const ledger = (data.ledger ?? []).slice(0, 20);

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <h1 className="pt-2 text-xl font-bold">Subscription</h1>
      <p className="muted mt-1 text-sm">Your plan, credits and billing history.</p>

      {data.promo_active && promo && (
        <div className="panel mt-5 border-purple-400/50 bg-purple-500/10 p-4" role="status">
          <p className="font-semibold text-purple-200">
            {promo.name || "Limited promotion"} — Pro at PKR{" "}
            {(promo.promo_price_pkr ?? 5250).toLocaleString("en-PK")} ({promo.discount_pct ?? 25}% off)
          </p>
          <p className="muted mt-1 text-sm">
            {promo.seats_remaining != null
              ? `${promo.seats_remaining} promotional seat${promo.seats_remaining === 1 ? "" : "s"} remaining. `
              : ""}
            Ends {fmtDate(promo.ends_at)}.
          </p>
        </div>
      )}

      <section className="panel mt-5 p-5" aria-label="Current plan">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold">{ent?.name ?? data.user.plan ?? "Free"}</h2>
            <p className="muted text-sm">
              Plan: {data.user.effective_plan ?? data.user.plan}
              {data.user.complimentary_premium ? " · Complimentary premium" : ""}
            </p>
          </div>
          <span className="badge badge-purple">{sub?.status ?? "active"}</span>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="muted">Credits balance</dt>
            <dd className="text-lg font-bold">{data.user.credits}</dd>
          </div>
          <div>
            <dt className="muted">Monthly credits</dt>
            <dd className="text-lg font-bold">{ent?.monthly_clips ?? "—"}</dd>
          </div>
          <div>
            <dt className="muted">Plan started</dt>
            <dd>{fmtDate(data.user.plan_started_at ?? sub?.started_at)}</dd>
          </div>
          <div>
            <dt className="muted">Plan ends</dt>
            <dd>{fmtDate(data.user.plan_ends_at ?? sub?.ends_at)}</dd>
          </div>
        </dl>
        <ul className="muted mt-4 grid gap-1.5 text-sm">
          <li>{ent?.manual_editing ? "✓ Manual editing" : "✗ No manual editing"}</li>
          <li>
            ✓{" "}
            {ent?.caption_style_list?.length
              ? `${ent.caption_style_list.length} caption style${ent.caption_style_list.length === 1 ? "" : "s"}`
              : `${ent?.caption_styles ?? "—"} caption styles`}
          </li>
          <li>
            {ent?.youtube_live_monitoring ? "✓ YouTube Live monitoring" : "✗ No YouTube Live monitoring"}
          </li>
          <li>{ent?.["4k_export"] ? "✓ 4K export" : "✗ No 4K export"}</li>
        </ul>
        {data.plan_change_note && (
          <p className="muted mt-4 rounded-xl border border-purple-400/30 bg-purple-500/10 px-3 py-2 text-sm">
            {data.plan_change_note}
          </p>
        )}
        <Link href="/pay" className="primary-button mt-5 block w-full text-center">
          Buy / Renew
        </Link>
      </section>

      <section className="mt-6" aria-label="Subscription history">
        <h2 className="text-lg font-bold">Subscription history</h2>
        {data.history.length === 0 ? (
          <p className="muted mt-2 text-sm">No previous subscriptions yet.</p>
        ) : (
          <ul className="mt-3 grid gap-3">
            {data.history.map((h) => (
              <li key={h.id} className="panel p-4">
                <div className="flex items-center justify-between gap-3">
                  <strong className="capitalize">{h.plan}</strong>
                  <span className="badge">{h.status}</span>
                </div>
                <p className="muted mt-1 text-sm">
                  {fmtDate(h.started_at)} → {fmtDate(h.ends_at)} · {h.credits_granted} credits
                  {h.is_promotional ? " · Promotional" : ""}
                  {h.promo_price_pkr != null
                    ? ` · PKR ${h.promo_price_pkr.toLocaleString("en-PK")}`
                    : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-6" aria-label="Credit ledger">
        <h2 className="text-lg font-bold">Credit ledger</h2>
        <p className="muted text-sm">Last 20 entries.</p>
        {ledger.length === 0 ? (
          <p className="muted mt-2 text-sm">No credit activity yet.</p>
        ) : (
          <ul className="mt-3 grid gap-3">
            {ledger.map((entry) => (
              <li key={entry.id} className="panel flex items-center justify-between gap-3 p-4">
                <div>
                  <p className="text-sm font-medium">{entry.reason}</p>
                  <p className="muted text-xs">{fmtDateTime(entry.created_at)}</p>
                </div>
                <span
                  className={`text-sm font-bold ${entry.delta >= 0 ? "text-green-400" : "text-red-400"}`}
                >
                  {entry.delta >= 0 ? "+" : ""}
                  {entry.delta}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </AppShell>
  );
}
