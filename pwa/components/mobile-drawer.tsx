"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import {
  CreditCard,
  Download,
  Film,
  History,
  Home,
  LifeBuoy,
  LogOut,
  Menu,
  Radio,
  Settings,
  ShieldCheck,
  Sparkles,
  X
} from "lucide-react";
import { apiFetch, isUnauthenticated } from "@/lib/api";

const links = [
  { href: "/dashboard", label: "Dashboard", Icon: Home },
  { href: "/create", label: "Quick Create", Icon: Sparkles },
  { href: "/clips", label: "My Clips", Icon: Film },
  { href: "/downloads", label: "Downloads", Icon: Download },
  { href: "/subscription", label: "Subscription", Icon: CreditCard },
  { href: "/payments", label: "Payment History", Icon: History },
  { href: "/monitoring", label: "YouTube Monitoring", Icon: Radio },
  { href: "/support", label: "Help & Support", Icon: LifeBuoy },
  { href: "/settings", label: "Settings", Icon: Settings }
];

export interface MobileDrawerProps {
  /** True only for the owner account — gates the Admin link. */
  isOwner: boolean;
}

export function MobileDrawer({ isOwner }: MobileDrawerProps) {
  const [open, setOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch (err) {
      // Even if logout fails (e.g. session already gone), leave the app.
      if (!isUnauthenticated(err)) {
        // Non-auth logout errors are ignored on purpose: the session cookie
        // is server-side and the user is leaving either way.
      }
    } finally {
      setOpen(false);
      setLoggingOut(false);
      router.push("/login");
      router.refresh();
    }
  }

  return (
    <>
      <header className="flex items-center justify-between px-4 py-4">
        <button
          aria-label="Open navigation"
          onClick={() => setOpen(true)}
          className="panel p-2"
        >
          <Menu size={21} />
        </button>
        <Link href="/dashboard" className="text-xl font-bold tracking-wide">
          Z <span className="text-purple-400">CLIPS</span>
        </Link>
        {/* Profile/avatar circle is display-only — it is NOT a menu trigger. */}
        <div
          aria-label="User profile"
          className="grid h-9 w-9 place-items-center rounded-full border border-purple-400/40 bg-purple-500/20 font-semibold"
        >
          Z
        </div>
      </header>

      {open && (
        <div className="fixed inset-0 z-50">
          <button
            aria-label="Close navigation overlay"
            onClick={() => setOpen(false)}
            className="drawer-backdrop"
          />
          <nav className="drawer-panel" aria-label="Main navigation">
            <div className="mb-8 flex items-center justify-between">
              <strong className="text-xl">
                Z <span className="text-purple-400">CLIPS</span>
              </strong>
              <button aria-label="Close menu" onClick={() => setOpen(false)} className="p-2">
                <X />
              </button>
            </div>
            <div className="grid gap-2">
              {links.map(({ href, label, Icon }) => (
                <Link
                  key={href}
                  href={href}
                  onClick={() => setOpen(false)}
                  aria-current={pathname === href ? "page" : undefined}
                  className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm hover:bg-purple-500/15 ${
                    pathname === href ? "bg-purple-500/15 text-white" : ""
                  }`}
                >
                  <Icon size={18} className="text-purple-300" /> {label}
                </Link>
              ))}
              {isOwner && (
                <Link
                  href="/admin"
                  onClick={() => setOpen(false)}
                  aria-current={pathname === "/admin" ? "page" : undefined}
                  className="flex items-center gap-3 rounded-xl px-3 py-3 text-sm hover:bg-purple-500/15"
                >
                  <ShieldCheck size={18} className="text-purple-300" /> Admin
                </Link>
              )}
            </div>
            <button
              onClick={handleLogout}
              disabled={loggingOut}
              className="btn-danger mt-8 flex w-full items-center justify-center gap-2"
            >
              <LogOut size={18} /> {loggingOut ? "Logging out…" : "Logout"}
            </button>
          </nav>
        </div>
      )}
    </>
  );
}
