"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/** GET /api/v1/pay/waiting/{paymentId} */

interface UserSummary {
  id: number;
  username: string;
  role: string;
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

function statusBadgeClass(status: string): string {
  const s = status.toLowerCase();
  if (s === "approved") return "border-green-500/50 bg-green-500/15 text-green-300";
  if (s === "rejected") return "border-red-500/50 bg-red-500/15 text-red-300";
  return "border-yellow-500/50 bg-yellow-500/15 text-yellow-200";
}

export default function PayWaitingPage() {
  const router = useRouter();
  const params = useParams();
  const paymentId = Array.isArray(params.paymentId) ? params.paymentId[0] : params.paymentId;

  const [user, setUser] = useState<UserSummary | null>(null);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (isRefresh = false) => {
      if (!paymentId) return;
      if (isRefresh) setChecking(true);
      else setLoading(true);
      setError(null);
      try {
        const me = await apiFetch<{ user: UserSummary }>("/me");
        setUser(me.user);
        const res = await apiFetch<{ payment: Payment }>(`/pay/waiting/${paymentId}`);
        setPayment(res.payment);
      } catch (err) {
        if (isUnauthenticated(err)) {
          router.push("/login");
          return;
        }
        if (err instanceof ApiError) setError(err.message);
        else setError("Could not load this payment. Please try again.");
      } finally {
        setLoading(false);
        setChecking(false);
      }
    },
    [paymentId, router]
  );

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <AppShell isOwner={false}>
        <div className="flex flex-col items-center justify-center py-20">
          <div className="spinner" role="status" aria-label="Loading payment status" />
          <p className="muted mt-3 text-sm">Checking your payment…</p>
        </div>
      </AppShell>
    );
  }

  if (error || !payment) {
    return (
      <AppShell isOwner={user?.role === "owner"}>
        <h1 className="pt-2 text-xl font-bold">Payment status</h1>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error ?? "Could not load this payment."}
        </p>
        <button type="button" onClick={() => load()} className="primary-button mt-4">
          Try again
        </button>
      </AppShell>
    );
  }

  const status = payment.status.toLowerCase();

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <h1 className="pt-2 text-xl font-bold">Payment status</h1>
      <p className="muted mt-1 text-sm">
        Your payment is reviewed by the owner. It does not activate your plan until it is approved.
      </p>

      <section className="panel mt-5 p-5" aria-label="Payment details">
        <div className="flex items-center justify-between gap-3">
          <span className={`badge ${statusBadgeClass(payment.status)}`}>
            {payment.status.charAt(0).toUpperCase() + payment.status.slice(1)}
          </span>
          <span className="muted text-xs">#{payment.id}</span>
        </div>
        <dl className="mt-4 grid gap-2 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="muted">Plan</dt>
            <dd className="font-semibold capitalize">{payment.plan}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="muted">Amount</dt>
            <dd className="font-semibold">PKR {payment.amount_pkr.toLocaleString("en-PK")}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="muted">Reference</dt>
            <dd className="break-all font-semibold">{payment.txn_id}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="muted">Submitted</dt>
            <dd>{fmtDateTime(payment.created_at)}</dd>
          </div>
          {payment.decided_at && (
            <div className="flex justify-between gap-3">
              <dt className="muted">Decided</dt>
              <dd>{fmtDateTime(payment.decided_at)}</dd>
            </div>
          )}
          {payment.is_promotional && (
            <div className="flex justify-between gap-3">
              <dt className="muted">Promotional</dt>
              <dd>Yes</dd>
            </div>
          )}
        </dl>

        {status === "approved" && (
          <div className="mt-4 rounded-xl border border-green-500/40 bg-green-500/10 px-3 py-2 text-sm text-green-300">
            Your payment was approved and your plan is active.
          </div>
        )}
        {status === "rejected" && (
          <div className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            Your payment was rejected.
            {payment.review_note ? (
              <>
                <br />
                Owner note: {payment.review_note}
              </>
            ) : (
              " No note was left."
            )}
          </div>
        )}
        {status === "pending" && (
          <p className="muted mt-4 text-sm">
            Waiting for the owner to verify your payment. This usually takes a little while —
            check again later.
          </p>
        )}
      </section>

      <div className="mt-5 grid gap-3">
        <button
          type="button"
          onClick={() => load(true)}
          disabled={checking}
          className="btn-secondary w-full"
        >
          {checking ? "Checking…" : "Check again"}
        </button>
        {status === "approved" && (
          <Link href="/subscription" className="primary-button block w-full text-center">
            View subscription
          </Link>
        )}
        {status === "rejected" && (
          <Link href="/pay" className="primary-button block w-full text-center">
            Resubmit payment
          </Link>
        )}
        <Link href="/payments" className="btn-secondary block w-full text-center">
          Payment history
        </Link>
      </div>
    </AppShell>
  );
}
