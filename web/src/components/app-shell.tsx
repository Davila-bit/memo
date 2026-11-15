"use client";

import {
  CalendarBlank,
  ChartBar,
  ChatCircleText,
  GearSix,
  House,
  ShieldCheck,
  type Icon,
} from "@phosphor-icons/react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError, onUnauthorized } from "@/lib/api";
import { cn } from "@/lib/cn";
import { IS_MOCK } from "@/lib/env";
import { useLive } from "@/lib/live";
import { useHearth } from "@/lib/store";
import { DemoClock } from "./demo-clock";
import { HearthMark } from "./hearth-mark";
import { SettingsSheet } from "./settings-sheet";
import { Toaster } from "./ui/toaster";

export const NAV: { href: string; label: string; icon: Icon }[] = [
  { href: "/", label: "Today", icon: House },
  { href: "/week", label: "Week", icon: ChartBar },
  { href: "/schedule", label: "Schedule", icon: CalendarBlank },
  { href: "/ask", label: "Ask", icon: ChatCircleText },
  { href: "/privacy", label: "Privacy", icon: ShieldCheck },
];

function useAuthGuard(): "checking" | "ok" {
  const router = useRouter();
  const pathname = usePathname();
  const [state, setState] = useState<"checking" | "ok">("checking");
  const setMe = useHearth((s) => s.setMe);
  const reset = useHearth((s) => s.reset);

  useEffect(() => {
    const toLogin = () => {
      reset();
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    };
    onUnauthorized(toLogin);
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (cancelled) return;
        setMe(me);
        setState("ok");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) toLogin();
        else setState("ok"); // offline: show the app with its own error state
      });
    return () => {
      cancelled = true;
      onUnauthorized(null);
    };
    // Only on first mount; later 401s come through onUnauthorized.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return state;
}

export function AppShell({ children }: { children: ReactNode }) {
  const auth = useAuthGuard();
  useLive(auth === "ok");
  const pathname = usePathname();
  const me = useHearth((s) => s.me);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const resident = me?.resident;

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      <a
        href="#main"
        className="sr-only z-[60] rounded-full bg-ink px-5 py-3 text-surface focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to main content
      </a>

      {/* Side rail (desktop) */}
      <aside className="sticky top-0 hidden h-dvh flex-col border-r border-line bg-surface/60 px-4 py-6 lg:flex">
        <Link href="/" className="flex items-center gap-2.5 rounded-xl px-2 py-1">
          <HearthMark className="size-8" />
          <span className="font-serif text-2xl font-medium tracking-tight">Memo</span>
        </Link>
        <nav aria-label="Main" className="mt-8">
          <ul className="flex flex-col gap-1">
            {NAV.map((n) => (
              <li key={n.href}>
                <NavLink {...n} active={isCurrent(pathname, n.href)} layout="rail" />
              </li>
            ))}
          </ul>
        </nav>
        <div className="mt-auto space-y-3 px-2 text-sm text-muted">
          {IS_MOCK ? <p className="rounded-xl border border-dashed border-line-strong/60 p-3">Practice mode: made-up data, no real calls.</p> : null}
          <p>Live only. Nothing is recorded.</p>
        </div>
      </aside>

      <div className="flex min-h-dvh min-w-0 flex-col">
        <header className="sticky top-0 z-30 border-b border-line/80 bg-paper/95 backdrop-blur-[2px]">
          <div className="mx-auto flex w-full max-w-3xl items-center gap-3 px-4 py-3 sm:px-6">
            <Link href="/" className="flex items-center gap-2 lg:hidden" aria-label="Memo, Today">
              <HearthMark className="size-8" />
            </Link>
            <div className="min-w-0 flex-1">
              <p className="text-[0.8125rem] leading-tight text-muted">{resident ? `${resident.family_name_for_her}’s day` : "Memo"}</p>
              <p className="truncate font-serif text-xl leading-tight font-medium">{resident?.name ?? " "}</p>
            </div>
            <DemoClock />
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="inline-flex size-11 items-center justify-center rounded-full text-ink hover:bg-ink/[0.06]"
              aria-label="Settings"
            >
              <GearSix aria-hidden className="size-6" weight="duotone" />
            </button>
          </div>
        </header>

        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-3xl flex-1 px-4 pt-5 pb-28 outline-none sm:px-6 lg:pb-12">
          {auth === "checking" ? <ShellSkeleton /> : children}
        </main>

        {/* Bottom tab bar (phones / tablets) */}
        <nav
          aria-label="Main"
          className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/97 pb-[env(safe-area-inset-bottom)] lg:hidden"
        >
          <ul className="mx-auto grid max-w-xl grid-cols-5">
            {NAV.map((n) => (
              <li key={n.href}>
                <NavLink {...n} active={isCurrent(pathname, n.href)} layout="tab" />
              </li>
            ))}
          </ul>
        </nav>
      </div>

      <SettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} />
      <Toaster />
    </div>
  );
}

function isCurrent(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function NavLink({ href, label, icon: I, active, layout }: { href: string; label: string; icon: Icon; active: boolean; layout: "tab" | "rail" }) {
  if (layout === "tab") {
    return (
      <Link
        href={href}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex min-h-16 flex-col items-center justify-center gap-0.5 text-[0.75rem] font-medium",
          active ? "text-ink" : "text-muted hover:text-ink",
        )}
      >
        <span className={cn("flex h-8 w-14 items-center justify-center rounded-full", active && "bg-sage-tint")}>
          <I aria-hidden className="size-6" weight={active ? "fill" : "regular"} />
        </span>
        {label}
      </Link>
    );
  }
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex min-h-11 items-center gap-3 rounded-xl px-3 text-[0.9375rem] font-medium",
        active ? "bg-sage-tint text-ink" : "text-muted hover:bg-ink/[0.04] hover:text-ink",
      )}
    >
      <I aria-hidden className="size-5" weight={active ? "fill" : "regular"} />
      {label}
    </Link>
  );
}

function ShellSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading" className="space-y-4">
      <div className="skeleton h-28 rounded-[1.25rem]" />
      <div className="skeleton h-14 rounded-full" />
      <div className="skeleton h-64 rounded-[1.25rem]" />
    </div>
  );
}
