"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";

/**
 * Public plans page — no login required (the signup page links here).
 * GET /api/v1/plans. If /api/v1/me returns 401 we still render the plans.
 */

interface PlanInfo {
  name: string;
  price_pkr: number;
  monthly_clips: number;
  manual_editing: boolean;
  caption_styles: number | string;
  youtube_live_monitoring: boolean;
  "4k_export": boolean;
  advanced_positioning: boolean;
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

interface PlansResponse {
  plans: { free: PlanInfo; lite: PlanInfo; pro: PlanInfo };
  promotion: Promotion | null;
  promo_active: boolean;
}

interface MeResponse {
  user: {
    id: number;
    username: string;
    role: string;
    plan: string;
  };
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "30 Nov 2026";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function captionStylesLabel(v: number | string): string {
  if (v === "all") return "All caption styles";
  if (typeof v === "number") return v === 1 ? "Simple captions" : `${v} caption styles`;
  return String(v);
}

export default function PlansPage() {
  const [data, setData] = useState<PlansResponse | null>(null);
  const [currentPlan, setCurrentPlan] = useState<string | null>(null);
  const [loggedIn, setLoggedIn] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const plans = await apiFetch<PlansResponse>("/plans");
      setData(plans);
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
      else setError("Could not load plans. Please try again.");
      setLoading(false);
      return;
    }
    // Best-effort sign-in check: a 401 here is fine, the page stays public.
    try {
      const me = await apiFetch<MeResponse>("/me");
      setLoggedIn(true);
      setCurrentPlan(me.user.plan);
    } catch (err) {
      if (!isUnauthenticated(err)) {
        // Non-auth failure checking /me: ignore, plans still render.
      }
      setLoggedIn(false);
      setCurrentPlan(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col items-center justify-center px-4">
        <div className="spinner" role="status" aria-label="Loading plans" />
        <p className="muted mt-3 text-sm">Loading plans…</p>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col items-center justify-center px-4 text-center">
        <h1 className="text-xl font-bold">Plans</h1>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error ?? "Could not load plans."}
        </p>
        <button type="button" onClick={load} className="primary-button mt-4">
          Try again
        </button>
        <Link href="/login" className="mt-4 text-sm text-purple-300">
          Back to login
        </Link>
      </main>
    );
  }

  const promo = data.promotion;
  const showPromo = data.promo_active && promo;
  const order: Array<{ key: "free" | "lite" | "pro"; plan: PlanInfo }> = [
    { key: "free", plan: data.plans.free },
    { key: "lite", plan: data.plans.lite },
    { key: "pro", plan: data.plans.pro }
  ];

  return (
    <main className="mx-auto min-h-screen w-full max-w-3xl px-4 pb-10">
      <header className="flex items-center justify-between py-4">
        <Link href="/login" className="text-xl font-bold tracking-wide">
          Z <span className="text-purple-400">CLIPS</span>
        </Link>
        {loggedIn ? (
          <Link href="/dashboard" className="btn-secondary px-3 py-2 text-sm">
            Dashboard
          </Link>
        ) : (
          <Link href="/login" className="btn-secondary px-3 py-2 text-sm">
            Login
          </Link>
        )}
      </header>

      <h1 className="text-2xl font-bold">Choose your plan</h1>
      <p className="muted mt-1 text-sm">
        AI-generated clips, manual editing and YouTube Live monitoring — pick the plan that fits you.
      </p>

      {showPromo && promo && (
        <div className="panel mt-5 border-purple-400/50 bg-purple-500/10 p-4" role="status">
          <p className="font-semibold text-purple-200">
            🎉 {promo.name || "Limited promotion"} — Pro at PKR{" "}
            {(promo.promo_price_pkr ?? 5250).toLocaleString("en-PK")} (
            {promo.discount_pct ?? 25}% off)
          </p>
          <p className="muted mt-1 text-sm">
            {promo.seats_remaining != null
              ? `${promo.seats_remaining} promotional seat${promo.seats_remaining === 1 ? "" : "s"} remaining. `
              : ""}
            Ends {fmtDate(promo.ends_at)}.
          </p>
        </div>
      )}

      <div className="mt-5 grid gap-4">
        {order.map(({ key, plan }) => {
          const isCurrent = currentPlan === key;
          const isPaid = key !== "free";
          const isPromoPro = showPromo && key === "pro";
          return (
            <article key={key} className="panel card p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-lg font-bold">{plan.name}</h2>
                  <p className="muted text-sm">{plan.monthly_clips} credits / month</p>
                </div>
                {isCurrent && <span className="badge badge-purple">Current plan</span>}
                {isPromoPro && promo && (
                  <span className="badge badge-purple">{promo.discount_pct ?? 25}% off</span>
                )}
              </div>

              <p className="mt-3">
                {isPromoPro && promo ? (
                  <>
                    <span className="text-2xl font-bold">
                      PKR {(promo.promo_price_pkr ?? 5250).toLocaleString("en-PK")}
                    </span>{" "}
                    <span className="muted text-sm line-through">
                      PKR {(promo.normal_price_pkr ?? plan.price_pkr).toLocaleString("en-PK")}
                    </span>
                    <span className="muted text-sm"> / month</span>
                  </>
                ) : (
                  <>
                    <span className="text-2xl font-bold">
                      PKR {plan.price_pkr.toLocaleString("en-PK")}
                    </span>
                    <span className="muted text-sm"> / month</span>
                  </>
                )}
              </p>

              <ul className="muted mt-4 grid gap-1.5 text-sm">
                <li>{plan.manual_editing ? "✓ Manual editing" : "✗ No manual editing"}</li>
                <li>✓ {captionStylesLabel(plan.caption_styles)}</li>
                <li>
                  {plan.youtube_live_monitoring
                    ? "✓ YouTube Live monitoring"
                    : "✗ No YouTube Live monitoring"}
                </li>
                <li>{plan["4k_export"] ? "✓ 4K export" : "✗ No 4K export"}</li>
                {plan.advanced_positioning && <li>✓ Advanced positioning</li>}
              </ul>

              <div className="mt-5">
                {isCurrent ? (
                  <button type="button" className="btn-secondary w-full" disabled>
                    Current plan
                  </button>
                ) : isPaid ? (
                  <Link
                    href={loggedIn ? `/pay?plan=${key}` : "/login"}
                    className="primary-button block w-full text-center"
                  >
                    Buy {plan.name}
                  </Link>
                ) : (
                  <Link
                    href={loggedIn ? "/dashboard" : "/signup"}
                    className="btn-secondary block w-full text-center"
                  >
                    Get started free
                  </Link>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <p className="muted mt-6 text-center text-xs">
        Unused credits carry forward. Paying never activates a plan instantly — the owner verifies
        every payment first.
      </p>
    </main>
  );
}
