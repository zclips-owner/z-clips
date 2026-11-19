"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/**
 * NayaPay payment page.
 * GET /api/v1/pay — server-computed amounts, QR availability, account details.
 * POST /api/v1/pay (multipart: plan, txn_id, screenshot).
 * The client NEVER sends an amount. Submission does NOT activate a plan.
 */

type PlanChoice = "lite" | "pro";

interface UserSummary {
  id: number;
  username: string;
  role: string;
  plan: string;
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

interface PayInfo {
  plans: { lite: { price_pkr: number }; pro: { price_pkr: number } };
  promotion: Promotion | null;
  promo_active: boolean;
  amounts: { lite: number; pro: number };
  qr_available: boolean;
  account_holder: string;
  number: string;
  instruction: string;
}

interface Payment {
  id: number;
  plan: string;
  amount_pkr: number;
  is_promotional: boolean;
  txn_id: string;
  status: string;
  review_note: string | null;
  created_at: string;
  decided_at: string | null;
}

const EXACT_INSTRUCTION =
  "If scanning the QR code does not show the account holder name Zain ul abideen, use the NayaPay number 03296195965 instead.";

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function PayPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawPlan = searchParams.get("plan");
  const plan: PlanChoice = rawPlan === "pro" ? "pro" : "lite";

  const [user, setUser] = useState<UserSummary | null>(null);
  const [info, setInfo] = useState<PayInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [txnId, setTxnId] = useState("");
  const [screenshot, setScreenshot] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await apiFetch<{ user: UserSummary }>("/me");
      setUser(me.user);
      const payInfo = await apiFetch<PayInfo>("/pay");
      setInfo(payInfo);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setError(err.message);
      else setError("Could not load payment details. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitError(null);

    if (!txnId.trim()) {
      setSubmitError("Please enter the transaction reference.");
      return;
    }
    if (txnId.trim().length > 120) {
      setSubmitError("Transaction reference must be 120 characters or fewer.");
      return;
    }
    if (!screenshot) {
      setSubmitError("Please upload a screenshot of your payment proof.");
      return;
    }
    if (!screenshot.type.startsWith("image/")) {
      setSubmitError("The proof must be an image file.");
      return;
    }

    setSubmitting(true);
    try {
      const form = new FormData();
      form.append("plan", plan);
      form.append("txn_id", txnId.trim());
      form.append("screenshot", screenshot);
      const res = await apiFetch<{ payment: Payment }>("/pay", { method: "POST", body: form });
      router.push(`/pay/waiting/${res.payment.id}`);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setSubmitError(err.message);
      else setSubmitError("Could not submit your payment. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <AppShell isOwner={false}>
        <div className="flex flex-col items-center justify-center py-20">
          <div className="spinner" role="status" aria-label="Loading payment details" />
          <p className="muted mt-3 text-sm">Loading payment details…</p>
        </div>
      </AppShell>
    );
  }

  if (error || !info) {
    return (
      <AppShell isOwner={user?.role === "owner"}>
        <h1 className="pt-2 text-xl font-bold">Pay with NayaPay</h1>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error ?? "Could not load payment details."}
        </p>
        <button type="button" onClick={load} className="primary-button mt-4">
          Try again
        </button>
      </AppShell>
    );
  }

  const amount = info.amounts?.[plan] ?? info.plans?.[plan]?.price_pkr;
  const planLabel = plan === "pro" ? "Pro" : "Lite";
  const isPromoPrice =
    info.promo_active && plan === "pro" && info.promotion?.promo_price_pkr != null;

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <h1 className="pt-2 text-xl font-bold">Pay with NayaPay</h1>
      <p className="muted mt-1 text-sm">
        Pay for the {planLabel} plan, then submit your payment proof below.
      </p>

      <div className="mt-4 flex gap-2" role="group" aria-label="Choose plan">
        <Link
          href="/pay?plan=lite"
          aria-current={plan === "lite" ? "true" : undefined}
          className={`flex-1 rounded-xl border px-3 py-3 text-center text-sm font-semibold ${
            plan === "lite" ? "border-purple-400 bg-purple-500/15" : "border-[var(--border)] bg-[var(--surface-2)]"
          }`}
        >
          Lite
        </Link>
        <Link
          href="/pay?plan=pro"
          aria-current={plan === "pro" ? "true" : undefined}
          className={`flex-1 rounded-xl border px-3 py-3 text-center text-sm font-semibold ${
            plan === "pro" ? "border-purple-400 bg-purple-500/15" : "border-[var(--border)] bg-[var(--surface-2)]"
          }`}
        >
          Pro
        </Link>
      </div>

      <section className="panel mt-5 p-5" aria-label="Amount to pay">
        <div className="flex items-baseline justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold">{planLabel} plan</h2>
            <p className="muted text-sm">Amount set by the server — you never type an amount.</p>
          </div>
          <p className="text-2xl font-bold">PKR {amount?.toLocaleString("en-PK") ?? "—"}</p>
        </div>
        {isPromoPrice && info.promotion && (
          <p className="mt-2 text-sm text-purple-200">
            Promotional price — {info.promotion.discount_pct}% off the normal PKR{" "}
            {info.promotion.normal_price_pkr.toLocaleString("en-PK")}.{" "}
            {info.promotion.seats_remaining != null
              ? `${info.promotion.seats_remaining} promotional seats remaining. `
              : ""}
            Ends {fmtDate(info.promotion.ends_at)}.
          </p>
        )}
      </section>

      <section className="panel mt-5 p-5 text-center" aria-label="NayaPay QR code">
        <h2 className="text-lg font-bold">Scan to pay</h2>
        {info.qr_available ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/api/v1/pay/qr"
              alt="NayaPay QR code for Z CLIPS payment"
              className="mx-auto mt-4 max-w-[260px] rounded-xl bg-white p-2"
            />
            <p className="mt-4 text-sm font-medium">{EXACT_INSTRUCTION}</p>
          </>
        ) : (
          <>
            <p className="mt-4 rounded-xl border border-yellow-500/40 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-200">
              QR code not yet provided by the owner.
            </p>
            <p className="mt-4 text-sm font-medium">{EXACT_INSTRUCTION}</p>
          </>
        )}
        <dl className="mt-4 grid gap-1 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="muted">Account holder</dt>
            <dd className="font-semibold">{info.account_holder || "Zain ul abideen"}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="muted">NayaPay number</dt>
            <dd className="font-semibold">{info.number || "03296195965"}</dd>
          </div>
        </dl>
      </section>

      <section className="panel mt-5 p-5" aria-label="Submit payment proof">
        <h2 className="text-lg font-bold">Submit your payment</h2>
        <form onSubmit={handleSubmit} className="mt-4 grid gap-4">
          <label className="grid gap-1.5 text-sm font-medium">
            Transaction reference
            <input
              type="text"
              value={txnId}
              onChange={(e) => setTxnId(e.target.value)}
              maxLength={120}
              required
              placeholder="e.g. NayaPay transaction ID"
            />
          </label>
          <label className="grid gap-1.5 text-sm font-medium">
            Proof screenshot (image, required)
            <input
              type="file"
              accept="image/*"
              required
              onChange={(e) => setScreenshot(e.target.files?.[0] ?? null)}
            />
          </label>
          {screenshot && (
            <p className="muted text-xs">Selected: {screenshot.name}</p>
          )}
          {submitError && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {submitError}
            </p>
          )}
          <button type="submit" className="primary-button w-full" disabled={submitting}>
            {submitting ? "Submitting…" : "Submit payment"}
          </button>
        </form>
        <p className="muted mt-4 text-sm">
          Submitting a payment does not activate your plan — the owner verifies it first.
        </p>
      </section>
    </AppShell>
  );
}

export default function PayPage() {
  return (
    <Suspense
      fallback={
        <AppShell isOwner={false}>
          <div className="flex flex-col items-center justify-center py-20">
            <div className="spinner" role="status" aria-label="Loading payment page" />
            <p className="muted mt-3 text-sm">Loading payment page…</p>
          </div>
        </AppShell>
      }
    >
      <PayPageInner />
    </Suspense>
  );
}
