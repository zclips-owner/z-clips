import { MobileDrawer } from "./mobile-drawer";

export interface AppShellProps {
  children: React.ReactNode;
  /** True only for the owner account — gates the Admin link in the drawer. */
  isOwner: boolean;
}

export function AppShell({ children, isOwner }: AppShellProps) {
  return (
    <main className="mx-auto min-h-screen w-full max-w-3xl">
      <MobileDrawer isOwner={isOwner} />
      <section className="px-4 pb-10">{children}</section>
    </main>
  );
}
