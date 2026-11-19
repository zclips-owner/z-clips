"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  CreditCard,
  Download,
  Film,
  History,
  LifeBuoy,
  Lock,
  Radio,
  Scissors,
  Sparkles
} from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { EmptyState, ErrorState, LoadingState } from "../clips/_components/clip-ui";
import {
  formatDate,
  formatDateTime,
  type Activity,
  type ClipDetail,
  type Entitlements,
  type Promotion,
  type Subscription,
  type UserSummary
} from "../clips/_components/types";

interface DashboardResponse {
  user: UserSummary;
  entitlements: Entitlements;
  credits: number;
  plan_name: string;
  subscription: Subscription | null;
  promotion: Promotion;
  promo_active: boolean;
  recent_activity: Activity[];
  clip_counts: { total: number; approved: number; candidates: number };
  yt_status: "available" | "locked";
}

interface SearchResponse {
  q: string;
  clips: ClipDetail[];
  users: unknown[];
}

const shortcuts = [
  { href: "/create", label: "Quick Create", Icon: Sparkles },
  { href: "/clips", label: "Quick Edit", sub: "Pick a candidate clip", Icon: Scissors },
  { href: "/clips", label: "My Clips", Icon: Film },
  { href: "/downloads", label: "Downloads", Icon: Download },
  { href: "/subscription", label: "Subscription", Icon: CreditCard },
  { href: "/payments", label: "Payment History", Icon: History },
  { href: "/monitoring", label: "YouTube Monitoring", Icon: Radio },
  { href: "/support", label: "Help & Support", Icon: LifeBuoy }
];

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<ClipDetail[] | null>(null);
  const [searching, setSearching] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch<DashboardResponse>("/dashboard");
      setData(res);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Could not load the dashboard.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  // Debounced search against GET /api/v1/search?q=
  useEffect(() => {
    const q = query.trim();
    if (q.length === 0) {
      setSearchResults(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const res = await apiFetch<SearchResponse>(
          `/search?q=${encodeURIComponent(q)}`
        );
        setSearchResults(res.clips);
      } catch (err) {
        if (isUnauthenticated(err)) {
          router.push("/login");
          return;
        }
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 400);
    return () => clearTimeout(t);
  }, [query, router]);

  const user = data?.user ?? null;
  const displayName = user?.display_name?.trim() || user?.username || "there";
  const subEndsAt = data?.subscription?.ends_at ?? user?.plan_ends_at ?? null;

  return (
    <AppShell isOwner={user?.role === "owner"}>
      {loading && <LoadingState label="Loading dashboard…" />}
      {!loading && error && <ErrorState message={error} onRetry={load} />}
      {!loading && !error && data && (
        <div className="grid gap-4 pt-2">
          <header>
            <h1 className="text-2xl font-bold">Welcome back, {displayName}</h1>
            <p className="muted mt-1 text-sm">
              {data.plan_name} plan · @{user?.username}
            </p>
          </header>

          <label className="grid gap-1.5 text-sm font-medium">
            Search your clips
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search clips by title…"
              aria-label="Search clips"
            />
          </label>
          {query.trim().length > 0 && (
            <div className="panel p-4">
              {searching && <p className="muted text-sm">Searching…</p>}
              {!searching && searchResults !== null && searchResults.length === 0 && (
                <p className="muted text-sm">No clips match “{query.trim()}”.</p>
              )}
              {!searching && searchResults !== null && searchResults.length > 0 && (
                <ul className="grid gap-2">
                  {searchResults.map((clip) => (
                    <li key={clip.id}>
                      <Link
                        href={`/clips/${clip.id}`}
                        className="block rounded-xl px-2 py-2 text-sm hover:bg-purple-500/10"
                      >
                        <span className="font-semibold">{clip.title}</span>
                        <span className="muted ml-2 text-xs">
                          {formatDate(clip.created_at)} · {clip.status}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {data.promo_active && data.promotion && (
            <div
              role="status"
              className="panel border-purple-400/50 bg-purple-500/15 p-4"
            >
              <p className="font-bold text-purple-200">
                {data.promotion.name || "Pro promotion"} — Pro for PKR{" "}
                {data.promotion.promo_price_pkr.toLocaleString()}
              </p>
              <p className="mt-1 text-sm text-purple-100/90">
                {data.promotion.seats_remaining} seats remaining
                {data.promotion.ends_at
                  ? ` · ends ${formatDate(data.promotion.ends_at)}`
                  : ""}
                {data.promotion.normal_price_pkr
                  ? ` · normally PKR ${data.promotion.normal_price_pkr.toLocaleString()}`
                  : ""}
              </p>
              <Link
                href="/subscription"
                className="mt-3 inline-block text-sm font-semibold text-purple-300 underline"
              >
                Upgrade to Pro
              </Link>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="panel p-4">
              <p className="muted text-xs font-semibold uppercase tracking-wide">
                Current plan
              </p>
              <p className="mt-1 text-lg font-bold">{data.plan_name}</p>
              {subEndsAt && (
                <p className="muted mt-1 text-xs">
                  Renews / ends {formatDate(subEndsAt)}
                </p>
              )}
            </div>
            <div className="panel p-4">
              <p className="muted text-xs font-semibold uppercase tracking-wide">
                Credits left
              </p>
              <p className="mt-1 text-3xl font-bold text-purple-300">
                {data.credits}
              </p>
              <p className="muted mt-1 text-xs">1 credit per clip job</p>
            </div>
          </div>

          <div className="panel grid grid-cols-3 divide-x divide-purple-500/10 p-4 text-center">
            <div>
              <p className="text-xl font-bold">{data.clip_counts.total}</p>
              <p className="muted text-xs">Total clips</p>
            </div>
            <div>
              <p className="text-xl font-bold">{data.clip_counts.candidates}</p>
              <p className="muted text-xs">Candidates</p>
            </div>
            <div>
              <p className="text-xl font-bold">{data.clip_counts.approved}</p>
              <p className="muted text-xs">Approved</p>
            </div>
          </div>

          <section aria-label="Shortcuts">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-purple-200">
              Shortcuts
            </h2>
            <div className="grid grid-cols-2 gap-3">
              {shortcuts.map(({ href, label, sub, Icon }) => {
                const locked = href === "/monitoring" && data.yt_status === "locked";
                return (
                  <Link key={label} href={href} className="panel card block p-4">
                    <Icon size={20} className="text-purple-300" />
                    <p className="mt-2 text-sm font-semibold">{label}</p>
                    {sub && <p className="muted text-xs">{sub}</p>}
                    {locked && (
                      <p className="mt-1 flex items-center gap-1 text-xs text-amber-300">
                        <Lock size={12} /> Locked — Pro required
                      </p>
                    )}
                  </Link>
                );
              })}
            </div>
          </section>

          <section aria-label="Recent activity">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-purple-200">
              Recent activity
            </h2>
            {data.recent_activity.length === 0 ? (
              <EmptyState message="No activity yet — create your first clip to get started." />
            ) : (
              <ul className="panel divide-y divide-purple-500/10 p-2">
                {data.recent_activity.map((item) => (
                  <li key={item.id} className="px-2 py-3">
                    <p className="text-sm font-semibold">{item.label}</p>
                    {item.detail && <p className="muted text-xs">{item.detail}</p>}
                    <p className="muted mt-0.5 text-xs">
                      {formatDateTime(item.created_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}
