"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/** GET /api/v1/payments — own payments only, newest first. */

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

export default function PaymentsPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await apiFetch<{ user: UserSummary }>("/me");
      setUser(me.user);
      const res = await apiFetch<{ payments: Payment[] }>("/payments");
      setPayments(res.payments ?? []);
    } catch (err) {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError) setError(err.message);
      else setError("Could not load your payment history. Please try again.");
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
          <div className="spinner" role="status" aria-label="Loading payment history" />
          <p className="muted mt-3 text-sm">Loading payment history…</p>
        </div>
      </AppShell>
    );
  }

  if (error) {
    return (
      <AppShell isOwner={user?.role === "owner"}>
        <h1 className="pt-2 text-xl font-bold">Payment History</h1>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </p>
        <button type="button" onClick={load} className="primary-button mt-4">
          Try again
        </button>
      </AppShell>
    );
  }

  return (
    <AppShell isOwner={user?.role === "owner"}>
      <h1 className="pt-2 text-xl font-bold">Payment History</h1>
      <p className="muted mt-1 text-sm">Every payment you have submitted, newest first.</p>

      <Link href="/pay" className="primary-button mt-5 block w-full text-center">
        Make a payment
      </Link>

      {payments.length === 0 ? (
        <div className="panel mt-5 p-6 text-center">
          <p className="font-semibold">No payments yet</p>
          <p className="muted mt-1 text-sm">
            You have not submitted any payments. Buy a plan to get started.
          </p>
        </div>
      ) : (
        <ul className="mt-5 grid gap-3">
          {payments.map((p) => (
            <li key={p.id} className="panel p-4">
              <div className="flex items-center justify-between gap-3">
                <strong className="capitalize">{p.plan} plan</strong>
                <span className={`badge ${statusBadgeClass(p.status)}`}>
                  {p.status.charAt(0).toUpperCase() + p.status.slice(1)}
                </span>
              </div>
              <dl className="mt-3 grid gap-1 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="muted">Amount</dt>
                  <dd className="font-semibold">PKR {p.amount_pkr.toLocaleString("en-PK")}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="muted">Reference</dt>
                  <dd className="break-all font-medium">{p.txn_id}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="muted">Date</dt>
                  <dd>{fmtDateTime(p.created_at)}</dd>
                </div>
                {p.is_promotional && (
                  <div className="flex justify-between gap-3">
                    <dt className="muted">Promotional</dt>
                    <dd>Yes</dd>
                  </div>
                )}
                {p.review_note && (
                  <div className="flex justify-between gap-3">
                    <dt className="muted">Owner note</dt>
                    <dd className="text-right">{p.review_note}</dd>
                  </div>
                )}
              </dl>
              <Link
                href={`/pay/waiting/${p.id}`}
                className="mt-3 block text-sm font-semibold text-purple-300"
              >
                View status →
              </Link>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
