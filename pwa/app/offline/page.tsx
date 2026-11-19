import { AppShell } from "@/components/app-shell";
import Link from "next/link";

export default function OfflinePage() {
  return (
    <AppShell isOwner={false}>
      <div className="panel mt-6 p-6 text-center">
        <h1 className="text-xl font-bold">You are offline</h1>
        <p className="muted mt-2 text-sm">
          Z CLIPS needs an internet connection to load your clips and account. Please reconnect and try
          again.
        </p>
        <Link href="/dashboard" className="primary-button mt-6 inline-block">
          Retry
        </Link>
      </div>
    </AppShell>
  );
}
