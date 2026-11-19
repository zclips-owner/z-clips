"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch, isUnauthenticated } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

/**
 * Owner-only admin. First GET /api/v1/me; non-owners see an "Access denied"
 * page (cosmetic only — the server enforces require_owner on every endpoint).
 * Tabs: Overview, Payments, Users, Support, Promotion, System, Settings.
 */

type Tab =
  | "overview"
  | "payments"
  | "users"
  | "support"
  | "promotion"
  | "system"
  | "settings";

interface UserSummary {
  id: number;
  username: string;
  email: string;
  display_name: string;
  role: string;
  plan: string;
  effective_plan?: string;
  credits: number;
  clips_used: number;
  plan_started_at: string | null;
  plan_ends_at: string | null;
  is_active: boolean;
  is_suspended: boolean;
  complimentary_premium: boolean;
  created_at: string;
}

interface UserPublic {
  id: number;
  username: string;
  email: string;
  display_name: string;
  role: string;
  plan: string;
  credits: number;
  clips_used: number;
  plan_ends_at: string | null;
  is_active: boolean;
  is_suspended: boolean;
  complimentary_premium: boolean;
  created_at: string;
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
  username?: string;
}

interface SupportThread {
  id: number;
  subject: string;
  status: string;
  created_at: string;
  updated_at: string;
  username?: string;
}

interface SupportMessage {
  id: number;
  thread_id: number;
  author_id: number;
  author_role: string;
  body: string;
  created_at: string;
}

interface Activity {
  id: number;
  kind: string;
  label: string;
  detail: string;
  created_at: string;
}

interface ModelInfo {
  name: string;
  version: string;
  size_mb: number;
  license: string;
  status: string;
  error: string | null;
  installed_at: string | null;
}

interface OverviewData {
  counts: {
    users: number;
    pending_payments: number;
    open_support: number;
    clips_total: number;
    jobs_queued: number;
  };
  recent_payments: Payment[];
  recent_activity: Activity[];
}

interface UserDetail {
  user: UserPublic;
  subscriptions: Subscription[];
  ledger: CreditLedger[];
  payments: Payment[];
}

const TABS: Array<{ key: Tab; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "payments", label: "Payments" },
  { key: "users", label: "Users" },
  { key: "support", label: "Support" },
  { key: "promotion", label: "Promotion" },
  { key: "system", label: "System" },
  { key: "settings", label: "Settings" }
];

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

/** ISO string → value for <input type="datetime-local"> (local time). */
function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function statusBadgeClass(status: string): string {
  const s = status.toLowerCase();
  if (s === "approved" || s === "open") return "border-green-500/50 bg-green-500/15 text-green-300";
  if (s === "rejected" || s === "closed") return "border-red-500/50 bg-red-500/15 text-red-300";
  return "border-yellow-500/50 bg-yellow-500/15 text-yellow-200";
}

function SpinnerBlock({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12">
      <div className="spinner" role="status" aria-label={label} />
      <p className="muted mt-3 text-sm">{label}…</p>
    </div>
  );
}

export default function AdminPage() {
  const router = useRouter();
  const [me, setMe] = useState<UserSummary | null>(null);
  const [checking, setChecking] = useState(true);
  const [denied, setDenied] = useState(false);
  const [meError, setMeError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");

  // ---- Overview ----
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [overviewError, setOverviewError] = useState<string | null>(null);

  // ---- Payments ----
  const [payFilter, setPayFilter] = useState<"pending" | "approved" | "rejected">("pending");
  const [payments, setPayments] = useState<Payment[]>([]);
  const [paymentsLoading, setPaymentsLoading] = useState(false);
  const [paymentsError, setPaymentsError] = useState<string | null>(null);
  const [selectedPaymentId, setSelectedPaymentId] = useState<number | null>(null);
  const [paymentDetail, setPaymentDetail] = useState<{ payment: Payment; user: UserPublic } | null>(null);
  const [paymentDetailLoading, setPaymentDetailLoading] = useState(false);
  const [paymentActionError, setPaymentActionError] = useState<string | null>(null);
  const [paymentActionBusy, setPaymentActionBusy] = useState(false);
  const [rejectNote, setRejectNote] = useState("");

  // ---- Users ----
  const [userQuery, setUserQuery] = useState("");
  const [users, setUsers] = useState<UserPublic[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [userDetail, setUserDetail] = useState<UserDetail | null>(null);
  const [userDetailLoading, setUserDetailLoading] = useState(false);
  const [userActionError, setUserActionError] = useState<string | null>(null);
  const [userActionBusy, setUserActionBusy] = useState(false);
  const [userActionMsg, setUserActionMsg] = useState<string | null>(null);
  const [planChoice, setPlanChoice] = useState("lite");
  const [creditDelta, setCreditDelta] = useState("");
  const [creditReason, setCreditReason] = useState("");
  const [extendDays, setExtendDays] = useState("");

  // ---- Support ----
  const [threads, setThreads] = useState<SupportThread[]>([]);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);
  const [selectedThreadId, setSelectedThreadId] = useState<number | null>(null);
  const [threadDetail, setThreadDetail] = useState<{
    thread: SupportThread;
    messages: SupportMessage[];
  } | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [replyBody, setReplyBody] = useState("");
  const [supportActionError, setSupportActionError] = useState<string | null>(null);
  const [supportActionBusy, setSupportActionBusy] = useState(false);

  // ---- Promotion ----
  const [promotion, setPromotion] = useState<Promotion | null>(null);
  const [promoActive, setPromoActive] = useState(false);
  const [promoLoading, setPromoLoading] = useState(false);
  const [promoError, setPromoError] = useState<string | null>(null);
  const [promoIsActive, setPromoIsActive] = useState(false);
  const [promoMaxSeats, setPromoMaxSeats] = useState("");
  const [promoEndsAt, setPromoEndsAt] = useState("");
  const [promoSaving, setPromoSaving] = useState(false);
  const [promoMsg, setPromoMsg] = useState<string | null>(null);

  // ---- System ----
  const [systemData, setSystemData] = useState<{
    health: unknown;
    models: ModelInfo[];
    storage: unknown;
  } | null>(null);
  const [systemLoading, setSystemLoading] = useState(false);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [installingModel, setInstallingModel] = useState<string | null>(null);
  const [systemMsg, setSystemMsg] = useState<string | null>(null);

  // ---- Owner settings ----
  const [ownerSettings, setOwnerSettings] = useState<Record<string, unknown>>({});
  const [ownerSettingsLoading, setOwnerSettingsLoading] = useState(false);
  const [ownerSettingsError, setOwnerSettingsError] = useState<string | null>(null);
  const [settingKey, setSettingKey] = useState("");
  const [settingValue, setSettingValue] = useState("");
  const [settingSaving, setSettingSaving] = useState(false);
  const [settingMsg, setSettingMsg] = useState<string | null>(null);

  const handleAuthError = useCallback(
    (err: unknown): boolean => {
      if (isUnauthenticated(err)) {
        router.push("/login");
        return true;
      }
      return false;
    },
    [router]
  );

  const loadOverview = useCallback(async () => {
    setOverviewLoading(true);
    setOverviewError(null);
    try {
      const res = await apiFetch<OverviewData>("/owner/overview");
      setOverview(res);
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setOverviewError(err.message);
      else setOverviewError("Could not load overview.");
    } finally {
      setOverviewLoading(false);
    }
  }, [handleAuthError]);

  const loadPayments = useCallback(async () => {
    setPaymentsLoading(true);
    setPaymentsError(null);
    try {
      const res = await apiFetch<{ payments: Payment[] }>(
        `/owner/payments?status=${payFilter}`
      );
      setPayments(res.payments ?? []);
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setPaymentsError(err.message);
      else setPaymentsError("Could not load payments.");
    } finally {
      setPaymentsLoading(false);
    }
  }, [payFilter, handleAuthError]);

  const loadPaymentDetail = useCallback(
    async (id: number) => {
      setPaymentDetailLoading(true);
      setPaymentActionError(null);
      try {
        const res = await apiFetch<{ payment: Payment; user: UserPublic }>(
          `/owner/payments/${id}`
        );
        setPaymentDetail(res);
      } catch (err) {
        if (handleAuthError(err)) return;
        if (err instanceof ApiError) setPaymentActionError(err.message);
        else setPaymentActionError("Could not load this payment.");
      } finally {
        setPaymentDetailLoading(false);
      }
    },
    [handleAuthError]
  );

  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    setUsersError(null);
    try {
      const q = userQuery.trim();
      const res = await apiFetch<{ users: UserPublic[] }>(
        q ? `/owner/users?q=${encodeURIComponent(q)}` : "/owner/users"
      );
      setUsers(res.users ?? []);
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setUsersError(err.message);
      else setUsersError("Could not load users.");
    } finally {
      setUsersLoading(false);
    }
  }, [userQuery, handleAuthError]);

  const loadUserDetail = useCallback(
    async (id: number) => {
      setUserDetailLoading(true);
      setUserActionError(null);
      setUserActionMsg(null);
      try {
        const res = await apiFetch<UserDetail>(`/owner/users/${id}`);
        setUserDetail(res);
        setPlanChoice(res.user.plan);
      } catch (err) {
        if (handleAuthError(err)) return;
        if (err instanceof ApiError) setUserActionError(err.message);
        else setUserActionError("Could not load this user.");
      } finally {
        setUserDetailLoading(false);
      }
    },
    [handleAuthError]
  );

  const loadSupport = useCallback(async () => {
    setSupportLoading(true);
    setSupportError(null);
    try {
      const res = await apiFetch<{ threads: SupportThread[] }>("/owner/support");
      setThreads(res.threads ?? []);
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setSupportError(err.message);
      else setSupportError("Could not load support threads.");
    } finally {
      setSupportLoading(false);
    }
  }, [handleAuthError]);

  const loadThreadDetail = useCallback(
    async (id: number) => {
      setThreadLoading(true);
      setSupportActionError(null);
      try {
        const res = await apiFetch<{ thread: SupportThread; messages: SupportMessage[] }>(
          `/owner/support/${id}`
        );
        setThreadDetail(res);
      } catch (err) {
        if (handleAuthError(err)) return;
        if (err instanceof ApiError) setSupportActionError(err.message);
        else setSupportActionError("Could not load this thread.");
      } finally {
        setThreadLoading(false);
      }
    },
    [handleAuthError]
  );

  const loadPromotion = useCallback(async () => {
    setPromoLoading(true);
    setPromoError(null);
    setPromoMsg(null);
    try {
      const res = await apiFetch<{ promotion: Promotion; promo_active: boolean }>(
        "/owner/promotion"
      );
      setPromotion(res.promotion);
      setPromoActive(res.promo_active);
      setPromoIsActive(res.promotion.is_active);
      setPromoMaxSeats(String(res.promotion.max_seats ?? ""));
      setPromoEndsAt(toLocalInput(res.promotion.ends_at));
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setPromoError(err.message);
      else setPromoError("Could not load the promotion.");
    } finally {
      setPromoLoading(false);
    }
  }, [handleAuthError]);

  const loadSystem = useCallback(async () => {
    setSystemLoading(true);
    setSystemError(null);
    setSystemMsg(null);
    try {
      const res = await apiFetch<{ health: unknown; models: ModelInfo[]; storage: unknown }>(
        "/owner/system"
      );
      setSystemData(res);
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setSystemError(err.message);
      else setSystemError("Could not load system status.");
    } finally {
      setSystemLoading(false);
    }
  }, [handleAuthError]);

  const loadOwnerSettings = useCallback(async () => {
    setOwnerSettingsLoading(true);
    setOwnerSettingsError(null);
    setSettingMsg(null);
    try {
      const res = await apiFetch<{ settings: Record<string, unknown> }>("/owner/settings");
      setOwnerSettings(res.settings ?? {});
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setOwnerSettingsError(err.message);
      else setOwnerSettingsError("Could not load settings.");
    } finally {
      setOwnerSettingsLoading(false);
    }
  }, [handleAuthError]);

  // Initial /me check — cosmetic owner gate only.
  useEffect(() => {
    (async () => {
      try {
        const res = await apiFetch<{ user: UserSummary }>("/me");
        setMe(res.user);
        if (res.user.role !== "owner") setDenied(true);
        else loadOverview();
      } catch (err) {
        if (isUnauthenticated(err)) {
          router.push("/login");
          return;
        }
        if (err instanceof ApiError) setMeError(err.message);
        else setMeError("Could not verify your account.");
      } finally {
        setChecking(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Lazy-load each tab the first time it is opened.
  useEffect(() => {
    if (denied || checking) return;
    if (tab === "overview" && !overview) loadOverview();
    if (tab === "payments" && payments.length === 0 && !paymentsError) loadPayments();
    if (tab === "users" && users.length === 0 && !usersError) loadUsers();
    if (tab === "support" && threads.length === 0 && !supportError) loadSupport();
    if (tab === "promotion" && !promotion) loadPromotion();
    if (tab === "system" && !systemData) loadSystem();
    if (tab === "settings" && Object.keys(ownerSettings).length === 0 && !ownerSettingsError)
      loadOwnerSettings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, denied, checking]);

  // Refetch payments when the status filter changes.
  useEffect(() => {
    if (!denied && !checking && tab === "payments") loadPayments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payFilter]);

  async function decidePayment(action: "approve" | "reject") {
    if (!selectedPaymentId || paymentActionBusy) return;
    if (action === "reject" && !rejectNote.trim()) {
      setPaymentActionError("Please add a note explaining the rejection.");
      return;
    }
    setPaymentActionBusy(true);
    setPaymentActionError(null);
    try {
      const res = await apiFetch<{ payment: Payment }>(
        `/owner/payments/${selectedPaymentId}/${action}`,
        {
          method: "POST",
          body: action === "reject" ? { note: rejectNote.trim() } : undefined
        }
      );
      setPaymentDetail((prev) => (prev ? { ...prev, payment: res.payment } : prev));
      setPayments((prev) =>
        prev.map((p) => (p.id === selectedPaymentId ? res.payment : p))
      );
      setRejectNote("");
    } catch (err) {
      if (handleAuthError(err)) return;
      // The server's refusal message (e.g. plan_change / double-approval) is shown verbatim.
      if (err instanceof ApiError) setPaymentActionError(err.message);
      else setPaymentActionError("That action failed. Please try again.");
    } finally {
      setPaymentActionBusy(false);
    }
  }

  async function userAction(
    endpoint: string,
    body: Record<string, unknown>,
    successMsg: string
  ) {
    if (!selectedUserId || userActionBusy) return;
    setUserActionBusy(true);
    setUserActionError(null);
    setUserActionMsg(null);
    try {
      const res = await apiFetch<{ user: UserPublic }>(
        `/owner/users/${selectedUserId}/${endpoint}`,
        { method: "POST", body }
      );
      setUserDetail((prev) => (prev ? { ...prev, user: res.user } : prev));
      setUserActionMsg(successMsg);
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setUserActionError(err.message);
      else setUserActionError("That action failed. Please try again.");
    } finally {
      setUserActionBusy(false);
    }
  }

  async function handleSavePromotion(e: React.FormEvent) {
    e.preventDefault();
    if (promoSaving) return;
    setPromoSaving(true);
    setPromoError(null);
    setPromoMsg(null);
    try {
      const body: Record<string, unknown> = { is_active: promoIsActive };
      const seats = Number(promoMaxSeats);
      if (promoMaxSeats.trim() !== "" && Number.isFinite(seats)) body.max_seats = seats;
      if (promoEndsAt) {
        const d = new Date(promoEndsAt);
        if (!Number.isNaN(d.getTime())) body.ends_at = d.toISOString();
      }
      const res = await apiFetch<{ promotion: Promotion }>("/owner/promotion", {
        method: "POST",
        body
      });
      setPromotion(res.promotion);
      setPromoActive(res.promotion.is_active);
      setPromoMsg("Promotion saved.");
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setPromoError(err.message);
      else setPromoError("Could not save the promotion.");
    } finally {
      setPromoSaving(false);
    }
  }

  async function handleInstallModel(name: string) {
    if (installingModel) return;
    setInstallingModel(name);
    setSystemMsg(null);
    setSystemError(null);
    try {
      const res = await apiFetch<{ model: ModelInfo }>(
        `/owner/system/models/${encodeURIComponent(name)}/install`,
        { method: "POST" }
      );
      setSystemData((prev) =>
        prev
          ? {
              ...prev,
              models: prev.models.map((m) => (m.name === name ? res.model : m))
            }
          : prev
      );
      setSystemMsg(`Install started for ${name} — status comes from the server, not assumed.`);
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setSystemError(err.message);
      else setSystemError("Could not start the install.");
    } finally {
      setInstallingModel(null);
    }
  }

  async function handleSaveOwnerSetting(e: React.FormEvent) {
    e.preventDefault();
    if (settingSaving) return;
    setSettingSaving(true);
    setSettingMsg(null);
    setOwnerSettingsError(null);
    try {
      await apiFetch("/owner/settings", {
        method: "POST",
        body: { key: settingKey.trim(), value: settingValue }
      });
      setSettingMsg("Setting saved.");
      setSettingKey("");
      setSettingValue("");
      loadOwnerSettings();
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setOwnerSettingsError(err.message);
      else setOwnerSettingsError("Could not save the setting.");
    } finally {
      setSettingSaving(false);
    }
  }

  async function handleSupportReply(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedThreadId || supportActionBusy) return;
    if (!replyBody.trim()) {
      setSupportActionError("Please write a reply first.");
      return;
    }
    setSupportActionBusy(true);
    setSupportActionError(null);
    try {
      const res = await apiFetch<{ message: SupportMessage }>(
        `/owner/support/${selectedThreadId}/reply`,
        { method: "POST", body: { body: replyBody.trim() } }
      );
      setThreadDetail((prev) =>
        prev ? { ...prev, messages: [...prev.messages, res.message] } : prev
      );
      setReplyBody("");
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setSupportActionError(err.message);
      else setSupportActionError("Could not send the reply.");
    } finally {
      setSupportActionBusy(false);
    }
  }

  async function handleCloseThread() {
    if (!selectedThreadId || supportActionBusy) return;
    setSupportActionBusy(true);
    setSupportActionError(null);
    try {
      const res = await apiFetch<{ thread: SupportThread }>(
        `/owner/support/${selectedThreadId}/close`,
        { method: "POST" }
      );
      setThreadDetail((prev) => (prev ? { ...prev, thread: res.thread } : prev));
      setThreads((prev) => prev.map((t) => (t.id === selectedThreadId ? res.thread : t)));
    } catch (err) {
      if (handleAuthError(err)) return;
      if (err instanceof ApiError) setSupportActionError(err.message);
      else setSupportActionError("Could not close the thread.");
    } finally {
      setSupportActionBusy(false);
    }
  }

  if (checking) {
    return (
      <AppShell isOwner={false}>
        <SpinnerBlock label="Checking access" />
      </AppShell>
    );
  }

  if (meError && !me) {
    return (
      <AppShell isOwner={false}>
        <h1 className="pt-2 text-xl font-bold">Admin</h1>
        <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {meError}
        </p>
      </AppShell>
    );
  }

  if (denied) {
    return (
      <AppShell isOwner={false}>
        <h1 className="pt-2 text-xl font-bold">Admin</h1>
        <div className="panel mt-5 p-6 text-center">
          <p className="text-lg font-bold">Access denied — owner only</p>
          <p className="muted mt-2 text-sm">
            This area is only for the owner of Z CLIPS. If you think this is a mistake, contact
            the owner.
          </p>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell isOwner={true}>
      <h1 className="pt-2 text-xl font-bold">Admin</h1>
      <p className="muted mt-1 text-sm">Owner dashboard — signed in as {me?.username}.</p>

      <div className="mt-4 flex gap-2 overflow-x-auto pb-1" role="tablist" aria-label="Admin sections">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`shrink-0 rounded-xl border px-3 py-2 text-sm font-semibold ${
              tab === t.key
                ? "border-purple-400 bg-purple-500/15"
                : "border-[var(--border)] bg-[var(--surface-2)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ---------- OVERVIEW ---------- */}
      {tab === "overview" && (
        <section className="mt-4" aria-label="Overview">
          {overviewLoading && <SpinnerBlock label="Loading overview" />}
          {overviewError && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {overviewError}
            </p>
          )}
          {overview && (
            <>
              <div className="grid grid-cols-2 gap-3">
                {(
                  [
                    ["Users", overview.counts.users],
                    ["Pending payments", overview.counts.pending_payments],
                    ["Open support", overview.counts.open_support],
                    ["Total clips", overview.counts.clips_total],
                    ["Jobs queued", overview.counts.jobs_queued]
                  ] as Array<[string, number]>
                ).map(([label, value]) => (
                  <div key={label} className="panel p-4">
                    <p className="muted text-xs">{label}</p>
                    <p className="text-2xl font-bold">{value}</p>
                  </div>
                ))}
              </div>

              <h2 className="mt-6 font-bold">Recent payments</h2>
              {overview.recent_payments.length === 0 ? (
                <p className="muted mt-2 text-sm">No recent payments.</p>
              ) : (
                <ul className="mt-3 grid gap-3">
                  {overview.recent_payments.map((p) => (
                    <li key={p.id} className="panel flex items-center justify-between gap-3 p-4">
                      <div>
                        <p className="text-sm font-semibold">
                          {p.username ?? "User"} · <span className="capitalize">{p.plan}</span> · PKR{" "}
                          {p.amount_pkr.toLocaleString("en-PK")}
                        </p>
                        <p className="muted text-xs">
                          {p.txn_id} · {fmtDateTime(p.created_at)}
                        </p>
                      </div>
                      <span className={`badge ${statusBadgeClass(p.status)}`}>{p.status}</span>
                    </li>
                  ))}
                </ul>
              )}

              <h2 className="mt-6 font-bold">Recent activity</h2>
              {overview.recent_activity.length === 0 ? (
                <p className="muted mt-2 text-sm">No recent activity.</p>
              ) : (
                <ul className="mt-3 grid gap-3">
                  {overview.recent_activity.map((a) => (
                    <li key={a.id} className="panel p-4">
                      <p className="text-sm font-semibold">{a.label}</p>
                      {a.detail && <p className="muted mt-1 text-xs">{a.detail}</p>}
                      <p className="muted mt-1 text-xs">{fmtDateTime(a.created_at)}</p>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      )}

      {/* ---------- PAYMENTS ---------- */}
      {tab === "payments" && (
        <section className="mt-4" aria-label="Payments">
          <div className="flex gap-2" role="group" aria-label="Filter payments by status">
            {(["pending", "approved", "rejected"] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setSelectedPaymentId(null);
                  setPaymentDetail(null);
                  setPayFilter(s);
                }}
                aria-pressed={payFilter === s}
                className={`flex-1 rounded-xl border px-2 py-2 text-sm font-semibold capitalize ${
                  payFilter === s
                    ? "border-purple-400 bg-purple-500/15"
                    : "border-[var(--border)] bg-[var(--surface-2)]"
                }`}
              >
                {s}
              </button>
            ))}
          </div>

          {paymentsLoading && <SpinnerBlock label="Loading payments" />}
          {paymentsError && (
            <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {paymentsError}
            </p>
          )}

          {!selectedPaymentId && !paymentsLoading && payments.length === 0 && !paymentsError && (
            <div className="panel mt-4 p-6 text-center">
              <p className="muted text-sm">No {payFilter} payments.</p>
            </div>
          )}

          {!selectedPaymentId && (
            <ul className="mt-4 grid gap-3">
              {payments.map((p) => (
                <li key={p.id} className="panel card p-4">
                  <button
                    type="button"
                    className="block w-full text-left"
                    onClick={() => {
                      setSelectedPaymentId(p.id);
                      loadPaymentDetail(p.id);
                    }}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <strong className="text-sm">
                        {p.username ?? "User"} · <span className="capitalize">{p.plan}</span>
                      </strong>
                      <span className={`badge ${statusBadgeClass(p.status)}`}>{p.status}</span>
                    </div>
                    <p className="muted mt-1 text-xs">
                      PKR {p.amount_pkr.toLocaleString("en-PK")} · {p.txn_id} ·{" "}
                      {fmtDateTime(p.created_at)}
                      {p.is_promotional ? " · Promotional" : ""}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {selectedPaymentId && (
            <div className="mt-4">
              <button
                type="button"
                onClick={() => {
                  setSelectedPaymentId(null);
                  setPaymentDetail(null);
                  setPaymentActionError(null);
                  setRejectNote("");
                }}
                className="text-sm text-purple-300"
              >
                ← Back to payments
              </button>
              {paymentDetailLoading && <SpinnerBlock label="Loading payment" />}
              {paymentDetail && (
                <div className="panel mt-3 p-5">
                  <div className="flex items-center justify-between gap-3">
                    <h2 className="font-bold">
                      Payment #{paymentDetail.payment.id} —{" "}
                      <span className="capitalize">{paymentDetail.payment.plan}</span>
                    </h2>
                    <span className={`badge ${statusBadgeClass(paymentDetail.payment.status)}`}>
                      {paymentDetail.payment.status}
                    </span>
                  </div>
                  <dl className="mt-4 grid gap-2 text-sm">
                    <div className="flex justify-between gap-3">
                      <dt className="muted">User</dt>
                      <dd className="font-semibold">
                        {paymentDetail.user.username} ({paymentDetail.user.email})
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Amount</dt>
                      <dd className="font-semibold">
                        PKR {paymentDetail.payment.amount_pkr.toLocaleString("en-PK")}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Reference</dt>
                      <dd className="break-all font-medium">{paymentDetail.payment.txn_id}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Submitted</dt>
                      <dd>{fmtDateTime(paymentDetail.payment.created_at)}</dd>
                    </div>
                    {paymentDetail.payment.decided_at && (
                      <div className="flex justify-between gap-3">
                        <dt className="muted">Decided</dt>
                        <dd>{fmtDateTime(paymentDetail.payment.decided_at)}</dd>
                      </div>
                    )}
                    {paymentDetail.payment.review_note && (
                      <div className="flex justify-between gap-3">
                        <dt className="muted">Review note</dt>
                        <dd className="text-right">{paymentDetail.payment.review_note}</dd>
                      </div>
                    )}
                  </dl>

                  <h3 className="mt-5 font-bold">Proof screenshot</h3>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`/api/v1/owner/payments/${paymentDetail.payment.id}/screenshot`}
                    alt="Payment proof screenshot"
                    className="mt-2 max-h-96 w-full rounded-xl border border-[var(--border)] object-contain bg-black"
                  />

                  {paymentDetail.payment.status.toLowerCase() === "pending" && (
                    <div className="mt-5 grid gap-3">
                      <label className="grid gap-1.5 text-sm font-medium">
                        Rejection note (required to reject)
                        <input
                          type="text"
                          value={rejectNote}
                          onChange={(e) => setRejectNote(e.target.value)}
                          placeholder="Why is this payment rejected?"
                        />
                      </label>
                      <div className="flex gap-3">
                        <button
                          type="button"
                          onClick={() => decidePayment("approve")}
                          disabled={paymentActionBusy}
                          className="primary-button flex-1"
                        >
                          {paymentActionBusy ? "Working…" : "Approve"}
                        </button>
                        <button
                          type="button"
                          onClick={() => decidePayment("reject")}
                          disabled={paymentActionBusy}
                          className="btn-danger flex-1"
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                  )}
                  {paymentActionError && (
                    <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                      {paymentActionError}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {/* ---------- USERS ---------- */}
      {tab === "users" && (
        <section className="mt-4" aria-label="Users">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setSelectedUserId(null);
              setUserDetail(null);
              loadUsers();
            }}
            className="flex gap-2"
          >
            <input
              type="search"
              value={userQuery}
              onChange={(e) => setUserQuery(e.target.value)}
              placeholder="Search by username or email…"
              aria-label="Search users"
            />
            <button type="submit" className="primary-button shrink-0">
              Search
            </button>
          </form>

          {usersLoading && <SpinnerBlock label="Loading users" />}
          {usersError && (
            <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {usersError}
            </p>
          )}

          {!selectedUserId && !usersLoading && users.length === 0 && !usersError && (
            <div className="panel mt-4 p-6 text-center">
              <p className="muted text-sm">No users found.</p>
            </div>
          )}

          {!selectedUserId && (
            <ul className="mt-4 grid gap-3">
              {users.map((u) => (
                <li key={u.id} className="panel card p-4">
                  <button
                    type="button"
                    className="block w-full text-left"
                    onClick={() => {
                      setSelectedUserId(u.id);
                      loadUserDetail(u.id);
                    }}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <strong className="text-sm">{u.username}</strong>
                      <span className="badge capitalize">{u.plan}</span>
                    </div>
                    <p className="muted mt-1 text-xs">
                      {u.email} · {u.credits} credits ·{" "}
                      {u.is_suspended ? "Suspended" : u.is_active ? "Active" : "Inactive"}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {selectedUserId && (
            <div className="mt-4">
              <button
                type="button"
                onClick={() => {
                  setSelectedUserId(null);
                  setUserDetail(null);
                  setUserActionError(null);
                  setUserActionMsg(null);
                }}
                className="text-sm text-purple-300"
              >
                ← Back to users
              </button>
              {userDetailLoading && <SpinnerBlock label="Loading user" />}
              {userDetail && (
                <div className="panel mt-3 p-5">
                  <h2 className="font-bold">
                    {userDetail.user.username}{" "}
                    <span className="muted font-normal">({userDetail.user.email})</span>
                  </h2>
                  <dl className="mt-3 grid gap-2 text-sm">
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Role</dt>
                      <dd className="capitalize">{userDetail.user.role}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Plan</dt>
                      <dd className="capitalize">{userDetail.user.plan}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Credits</dt>
                      <dd>{userDetail.user.credits}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Clips used</dt>
                      <dd>{userDetail.user.clips_used}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Plan ends</dt>
                      <dd>{fmtDate(userDetail.user.plan_ends_at)}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="muted">Status</dt>
                      <dd>
                        {userDetail.user.is_suspended
                          ? "Suspended"
                          : userDetail.user.is_active
                            ? "Active"
                            : "Inactive"}
                        {userDetail.user.complimentary_premium ? " · Complimentary premium" : ""}
                      </dd>
                    </div>
                  </dl>

                  {userActionError && (
                    <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                      {userActionError}
                    </p>
                  )}
                  {userActionMsg && (
                    <p role="status" className="mt-4 rounded-xl border border-green-500/40 bg-green-500/10 px-3 py-2 text-sm text-green-300">
                      {userActionMsg}
                    </p>
                  )}

                  <div className="mt-5 grid gap-4">
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          userAction(
                            "status",
                            { is_active: !userDetail.user.is_active },
                            userDetail.user.is_active ? "User deactivated." : "User activated."
                          )
                        }
                        disabled={userActionBusy}
                        className="btn-secondary flex-1 text-sm"
                      >
                        {userDetail.user.is_active ? "Deactivate" : "Activate"}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          userAction(
                            "status",
                            { is_suspended: !userDetail.user.is_suspended },
                            userDetail.user.is_suspended ? "User unsuspended." : "User suspended."
                          )
                        }
                        disabled={userActionBusy}
                        className="btn-danger flex-1 text-sm"
                      >
                        {userDetail.user.is_suspended ? "Unsuspend" : "Suspend"}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          userAction(
                            "complimentary",
                            { enable: !userDetail.user.complimentary_premium },
                            userDetail.user.complimentary_premium
                              ? "Complimentary premium removed."
                              : "Complimentary premium enabled."
                          )
                        }
                        disabled={userActionBusy}
                        className="btn-secondary flex-1 text-sm"
                      >
                        {userDetail.user.complimentary_premium ? "Remove complimentary" : "Complimentary"}
                      </button>
                    </div>

                    <div className="grid gap-2 rounded-xl border border-[var(--border)] p-3">
                      <p className="text-sm font-semibold">Change plan</p>
                      <p className="muted text-xs">
                        ⚠️ Plan changes are audited. Only change a plan when the user has paid or
                        you have a clear reason.
                      </p>
                      <div className="flex gap-2">
                        <select
                          value={planChoice}
                          onChange={(e) => setPlanChoice(e.target.value)}
                          aria-label="New plan"
                        >
                          <option value="free">Free</option>
                          <option value="lite">Lite</option>
                          <option value="pro">Pro</option>
                        </select>
                        <button
                          type="button"
                          onClick={() =>
                            userAction("plan", { plan: planChoice }, `Plan changed to ${planChoice}.`)
                          }
                          disabled={userActionBusy}
                          className="primary-button shrink-0"
                        >
                          Apply
                        </button>
                      </div>
                    </div>

                    <div className="grid gap-2 rounded-xl border border-[var(--border)] p-3">
                      <p className="text-sm font-semibold">Adjust credits</p>
                      <div className="flex gap-2">
                        <input
                          type="number"
                          value={creditDelta}
                          onChange={(e) => setCreditDelta(e.target.value)}
                          placeholder="Delta (e.g. 10 or -5)"
                          aria-label="Credit delta"
                        />
                        <input
                          type="text"
                          value={creditReason}
                          onChange={(e) => setCreditReason(e.target.value)}
                          placeholder="Reason"
                          aria-label="Credit reason"
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          const delta = Number(creditDelta);
                          if (!Number.isFinite(delta) || delta === 0) {
                            setUserActionError("Enter a non-zero credit delta.");
                            return;
                          }
                          if (!creditReason.trim()) {
                            setUserActionError("Enter a reason for the credit adjustment.");
                            return;
                          }
                          userAction(
                            "credits",
                            { delta, reason: creditReason.trim() },
                            "Credits adjusted."
                          );
                        }}
                        disabled={userActionBusy}
                        className="btn-secondary"
                      >
                        Adjust credits
                      </button>
                    </div>

                    <div className="grid gap-2 rounded-xl border border-[var(--border)] p-3">
                      <p className="text-sm font-semibold">Extend plan</p>
                      <div className="flex gap-2">
                        <input
                          type="number"
                          min={1}
                          value={extendDays}
                          onChange={(e) => setExtendDays(e.target.value)}
                          placeholder="Days"
                          aria-label="Days to extend"
                        />
                        <button
                          type="button"
                          onClick={() => {
                            const days = Number(extendDays);
                            if (!Number.isFinite(days) || days <= 0) {
                              setUserActionError("Enter a positive number of days.");
                              return;
                            }
                            userAction("extend", { days }, `Plan extended by ${days} days.`);
                          }}
                          disabled={userActionBusy}
                          className="btn-secondary shrink-0"
                        >
                          Extend
                        </button>
                      </div>
                    </div>
                  </div>

                  <h3 className="mt-6 font-bold">Subscriptions</h3>
                  {userDetail.subscriptions.length === 0 ? (
                    <p className="muted mt-2 text-sm">No subscriptions.</p>
                  ) : (
                    <ul className="mt-2 grid gap-2">
                      {userDetail.subscriptions.map((s) => (
                        <li key={s.id} className="rounded-xl border border-[var(--border)] p-3 text-sm">
                          <span className="capitalize font-semibold">{s.plan}</span> · {s.status} ·{" "}
                          {fmtDate(s.started_at)} → {fmtDate(s.ends_at)} · {s.credits_granted} credits
                        </li>
                      ))}
                    </ul>
                  )}

                  <h3 className="mt-6 font-bold">Credit ledger</h3>
                  {userDetail.ledger.length === 0 ? (
                    <p className="muted mt-2 text-sm">No ledger entries.</p>
                  ) : (
                    <ul className="mt-2 grid gap-2">
                      {userDetail.ledger.map((l) => (
                        <li key={l.id} className="flex justify-between gap-3 rounded-xl border border-[var(--border)] p-3 text-sm">
                          <span>
                            {l.reason}
                            <span className="muted block text-xs">{fmtDateTime(l.created_at)}</span>
                          </span>
                          <span className={`font-bold ${l.delta >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {l.delta >= 0 ? "+" : ""}
                            {l.delta}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}

                  <h3 className="mt-6 font-bold">Payments</h3>
                  {userDetail.payments.length === 0 ? (
                    <p className="muted mt-2 text-sm">No payments.</p>
                  ) : (
                    <ul className="mt-2 grid gap-2">
                      {userDetail.payments.map((p) => (
                        <li key={p.id} className="flex justify-between gap-3 rounded-xl border border-[var(--border)] p-3 text-sm">
                          <span>
                            <span className="capitalize font-semibold">{p.plan}</span> · PKR{" "}
                            {p.amount_pkr.toLocaleString("en-PK")} · {p.txn_id}
                            <span className="muted block text-xs">{fmtDateTime(p.created_at)}</span>
                          </span>
                          <span className={`badge ${statusBadgeClass(p.status)}`}>{p.status}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {/* ---------- SUPPORT ---------- */}
      {tab === "support" && (
        <section className="mt-4" aria-label="Support">
          {supportLoading && <SpinnerBlock label="Loading support threads" />}
          {supportError && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {supportError}
            </p>
          )}

          {!selectedThreadId && !supportLoading && threads.length === 0 && !supportError && (
            <div className="panel mt-4 p-6 text-center">
              <p className="muted text-sm">No support threads.</p>
            </div>
          )}

          {!selectedThreadId && (
            <ul className="mt-4 grid gap-3">
              {threads.map((t) => (
                <li key={t.id} className="panel card p-4">
                  <button
                    type="button"
                    className="block w-full text-left"
                    onClick={() => {
                      setSelectedThreadId(t.id);
                      loadThreadDetail(t.id);
                    }}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <strong className="text-sm">{t.subject}</strong>
                      <span className={`badge ${statusBadgeClass(t.status)}`}>{t.status}</span>
                    </div>
                    <p className="muted mt-1 text-xs">
                      {t.username ? `${t.username} · ` : ""}Updated {fmtDateTime(t.updated_at)}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {selectedThreadId && (
            <div className="mt-4">
              <button
                type="button"
                onClick={() => {
                  setSelectedThreadId(null);
                  setThreadDetail(null);
                  setSupportActionError(null);
                  setReplyBody("");
                }}
                className="text-sm text-purple-300"
              >
                ← Back to threads
              </button>
              {threadLoading && <SpinnerBlock label="Loading thread" />}
              {threadDetail && (
                <div className="panel mt-3 p-5">
                  <div className="flex items-start justify-between gap-3">
                    <h2 className="font-bold">{threadDetail.thread.subject}</h2>
                    <span className={`badge ${statusBadgeClass(threadDetail.thread.status)}`}>
                      {threadDetail.thread.status}
                    </span>
                  </div>
                  <p className="muted mt-1 text-xs">
                    {threadDetail.thread.username ? `${threadDetail.thread.username} · ` : ""}
                    Opened {fmtDateTime(threadDetail.thread.created_at)}
                  </p>
                  <ul className="mt-4 grid gap-3">
                    {threadDetail.messages.map((m) => (
                      <li key={m.id} className="rounded-xl border border-[var(--border)] p-3">
                        <div className="flex items-center justify-between gap-3">
                          <span className={`badge ${m.author_role === "owner" ? "badge-purple" : ""}`}>
                            {m.author_role === "owner" ? "Owner" : m.author_role}
                          </span>
                          <span className="muted text-xs">{fmtDateTime(m.created_at)}</span>
                        </div>
                        <p className="mt-2 whitespace-pre-wrap text-sm">{m.body}</p>
                      </li>
                    ))}
                  </ul>
                  {threadDetail.messages.length === 0 && (
                    <p className="muted mt-4 text-sm">No messages yet.</p>
                  )}
                  {supportActionError && (
                    <p role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                      {supportActionError}
                    </p>
                  )}
                  {threadDetail.thread.status.toLowerCase() !== "closed" ? (
                    <>
                      <form onSubmit={handleSupportReply} className="mt-4 grid gap-3">
                        <label className="grid gap-1.5 text-sm font-medium">
                          Reply as owner
                          <textarea
                            value={replyBody}
                            onChange={(e) => setReplyBody(e.target.value)}
                            required
                            rows={4}
                            placeholder="Write your reply…"
                          />
                        </label>
                        <button type="submit" className="primary-button w-full" disabled={supportActionBusy}>
                          {supportActionBusy ? "Sending…" : "Send reply"}
                        </button>
                      </form>
                      <button
                        type="button"
                        onClick={handleCloseThread}
                        disabled={supportActionBusy}
                        className="btn-secondary mt-3 w-full"
                      >
                        Close thread
                      </button>
                    </>
                  ) : (
                    <p className="muted mt-4 text-sm">This thread is closed.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {/* ---------- PROMOTION ---------- */}
      {tab === "promotion" && (
        <section className="mt-4" aria-label="Promotion">
          {promoLoading && <SpinnerBlock label="Loading promotion" />}
          {promoError && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {promoError}
            </p>
          )}
          {promotion && (
            <>
              <div className="panel p-5">
                <h2 className="font-bold">{promotion.name || "Promotion"}</h2>
                <dl className="mt-3 grid gap-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="muted">Status</dt>
                    <dd>{promoActive ? "Active" : "Inactive"}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="muted">Discount</dt>
                    <dd>{promotion.discount_pct}%</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="muted">Promo price (Pro)</dt>
                    <dd>PKR {promotion.promo_price_pkr.toLocaleString("en-PK")}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="muted">Normal price (Pro)</dt>
                    <dd>PKR {promotion.normal_price_pkr.toLocaleString("en-PK")}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="muted">Seats</dt>
                    <dd>
                      {promotion.seats_used} used · {promotion.seats_reserved} reserved ·{" "}
                      {promotion.seats_remaining} remaining of {promotion.max_seats}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="muted">Ends</dt>
                    <dd>{fmtDateTime(promotion.ends_at)}</dd>
                  </div>
                </dl>
              </div>

              <form onSubmit={handleSavePromotion} className="panel mt-4 grid gap-4 p-5">
                <h2 className="font-bold">Edit promotion</h2>
                <label className="flex items-center gap-2 text-sm font-medium">
                  <input
                    type="checkbox"
                    checked={promoIsActive}
                    onChange={(e) => setPromoIsActive(e.target.checked)}
                    className="h-5 w-5 shrink-0"
                  />
                  Promotion active
                </label>
                <label className="grid gap-1.5 text-sm font-medium">
                  Max seats
                  <input
                    type="number"
                    min={0}
                    value={promoMaxSeats}
                    onChange={(e) => setPromoMaxSeats(e.target.value)}
                  />
                </label>
                <label className="grid gap-1.5 text-sm font-medium">
                  Ends at
                  <input
                    type="datetime-local"
                    value={promoEndsAt}
                    onChange={(e) => setPromoEndsAt(e.target.value)}
                  />
                </label>
                {promoMsg && (
                  <p role="status" className="rounded-xl border border-green-500/40 bg-green-500/10 px-3 py-2 text-sm text-green-300">
                    {promoMsg}
                  </p>
                )}
                <button type="submit" className="primary-button w-full" disabled={promoSaving}>
                  {promoSaving ? "Saving…" : "Save promotion"}
                </button>
              </form>
            </>
          )}
        </section>
      )}

      {/* ---------- SYSTEM ---------- */}
      {tab === "system" && (
        <section className="mt-4" aria-label="System">
          {systemLoading && <SpinnerBlock label="Loading system status" />}
          {systemError && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {systemError}
            </p>
          )}
          {systemMsg && (
            <p role="status" className="rounded-xl border border-green-500/40 bg-green-500/10 px-3 py-2 text-sm text-green-300">
              {systemMsg}
            </p>
          )}
          {systemData && (
            <>
              <div className="panel p-5">
                <h2 className="font-bold">Health</h2>
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded-xl bg-black/40 p-3 text-xs">
                  {JSON.stringify(systemData.health, null, 2)}
                </pre>
              </div>

              <div className="panel mt-4 p-5">
                <h2 className="font-bold">Models</h2>
                {systemData.models.length === 0 ? (
                  <p className="muted mt-2 text-sm">No models reported by the server.</p>
                ) : (
                  <ul className="mt-3 grid gap-3">
                    {systemData.models.map((m) => (
                      <li key={m.name} className="rounded-xl border border-[var(--border)] p-3">
                        <div className="flex items-center justify-between gap-3">
                          <strong className="text-sm">{m.name}</strong>
                          <span className="badge">{m.status}</span>
                        </div>
                        <p className="muted mt-1 text-xs">
                          {m.version ? `v${m.version} · ` : ""}
                          {m.size_mb != null ? `${m.size_mb} MB · ` : ""}
                          {m.license ?? ""}
                        </p>
                        {m.error && <p className="mt-1 text-xs text-red-300">{m.error}</p>}
                        {m.installed_at && (
                          <p className="muted mt-1 text-xs">Installed {fmtDateTime(m.installed_at)}</p>
                        )}
                        <button
                          type="button"
                          onClick={() => handleInstallModel(m.name)}
                          disabled={installingModel === m.name || m.status === "installed"}
                          className="btn-secondary mt-3 w-full text-sm"
                        >
                          {installingModel === m.name
                            ? "Installing…"
                            : m.status === "installed"
                              ? "Installed"
                              : "Install"}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="muted mt-3 text-xs">
                  A model is only shown as installed when the server reports it as installed.
                </p>
              </div>

              <div className="panel mt-4 p-5">
                <h2 className="font-bold">Storage</h2>
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded-xl bg-black/40 p-3 text-xs">
                  {JSON.stringify(systemData.storage, null, 2)}
                </pre>
              </div>

              <button type="button" onClick={loadSystem} className="btn-secondary mt-4 w-full">
                Refresh system status
              </button>
            </>
          )}
        </section>
      )}

      {/* ---------- OWNER SETTINGS ---------- */}
      {tab === "settings" && (
        <section className="mt-4" aria-label="Owner settings">
          {ownerSettingsLoading && <SpinnerBlock label="Loading settings" />}
          {ownerSettingsError && (
            <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {ownerSettingsError}
            </p>
          )}
          {!ownerSettingsLoading && Object.keys(ownerSettings).length === 0 && !ownerSettingsError && (
            <div className="panel p-6 text-center">
              <p className="muted text-sm">No owner settings stored yet.</p>
            </div>
          )}
          {Object.keys(ownerSettings).length > 0 && (
            <ul className="grid gap-3">
              {Object.entries(ownerSettings).map(([key, value]) => (
                <li key={key} className="panel flex items-center justify-between gap-3 p-4">
                  <strong className="break-all text-sm">{key}</strong>
                  <span className="muted break-all text-right text-sm">{String(value)}</span>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={handleSaveOwnerSetting} className="panel mt-4 grid gap-4 p-5">
            <h2 className="font-bold">Set a value</h2>
            <p className="muted text-xs">
              Owner-only keys. Never store secrets here — secrets are never shown or accepted
              through this screen.
            </p>
            <label className="grid gap-1.5 text-sm font-medium">
              Key
              <input
                type="text"
                value={settingKey}
                onChange={(e) => setSettingKey(e.target.value)}
                required
                placeholder="e.g. nayapay_number"
              />
            </label>
            <label className="grid gap-1.5 text-sm font-medium">
              Value
              <input
                type="text"
                value={settingValue}
                onChange={(e) => setSettingValue(e.target.value)}
                required
                placeholder="New value"
              />
            </label>
            {settingMsg && (
              <p role="status" className="rounded-xl border border-green-500/40 bg-green-500/10 px-3 py-2 text-sm text-green-300">
                {settingMsg}
              </p>
            )}
            <button type="submit" className="primary-button w-full" disabled={settingSaving}>
              {settingSaving ? "Saving…" : "Save setting"}
            </button>
          </form>
        </section>
      )}
    </AppShell>
  );
}
