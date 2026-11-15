"use client";

import { ArrowClockwise, NotePencil, PhoneCall, WifiSlash } from "@phosphor-icons/react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import type { Alert } from "@/lib/contracts";
import { formatDay, isActive } from "@/lib/format";
import { refreshToday } from "@/lib/live";
import { useHearth } from "@/lib/store";
import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { AddNoteSheet } from "./add-note-sheet";
import { AlertCard } from "./alert-card";
import { CheckinSheet } from "./checkin-sheet";
import { LiveCheckin } from "./live-checkin";
import { TodaySummary } from "./summary";
import { Timeline } from "./timeline";

export function TodayView() {
  const today = useHearth((s) => s.today);
  const schedule = useHearth((s) => s.schedule);
  const handled = useHearth((s) => s.handledAlerts);
  const loadError = useHearth((s) => s.loadError);
  const checkin = useHearth((s) => s.checkin);
  const dismissed = useHearth((s) => s.resultDismissed);
  const [sheet, setSheet] = useState(false);
  const [note, setNote] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const retry = async () => {
    setRetrying(true);
    await refreshToday();
    setRetrying(false);
  };

  if (!today) {
    return loadError ? <Unreachable onRetry={retry} busy={retrying} /> : <TodaySkeleton />;
  }

  const tz = today.resident.timezone;
  const running = isActive(checkin);
  // When a finished check-in raised an alert, the alert card carries the result; don't repeat it.
  const hasAlert = !!checkin && [...today.open_alerts, ...handled].some((a) => a.checkin_id === checkin.id);
  const showCard = checkin && (running || (!dismissed && !hasAlert));

  return (
    <div className="space-y-6">
      <Suspense fallback={null}><AlertFocus alerts={today.open_alerts} /></Suspense>
      {loadError ? (
        <div role="status" className="flex items-center gap-3 rounded-2xl border border-amber-fill/60 bg-amber-tint px-4 py-3 text-[0.9375rem]">
          <WifiSlash aria-hidden className="size-5 shrink-0 text-amber-ink" weight="bold" />
          <span className="flex-1">Couldn&apos;t reach Hearth. Trying again…</span>
          <Button variant="quiet" size="sm" onClick={retry} disabled={retrying}>
            Retry
          </Button>
        </div>
      ) : null}

      {today.open_alerts.length > 0 ? (
        <section aria-label="Needs you" className="space-y-4">
          {today.open_alerts.map((a) => (
            <AlertCard key={a.id} alert={a} />
          ))}
        </section>
      ) : null}

      {showCard ? <LiveCheckin checkin={checkin} events={today.events} /> : null}

      <TodaySummary
        today={today}
        schedule={schedule}
        actions={
          <div className="flex flex-col items-stretch gap-1.5 sm:items-end">
            <Button size="lg" onClick={() => setSheet(true)} disabled={running} aria-describedby={running ? "checkin-running" : undefined}>
              <PhoneCall aria-hidden className="size-5" weight="fill" />
              Check on her now
            </Button>
            {running ? (
              <p id="checkin-running" className="text-sm text-muted">
                A check-in is happening now. You can start another when it ends.
              </p>
            ) : null}
          </div>
        }
      />

      <section aria-labelledby="timeline-heading">
        <div className="mb-4 flex items-end justify-between gap-3">
          <div>
            <h2 id="timeline-heading" className="font-serif text-[1.625rem] leading-tight font-medium">
              Today
            </h2>
            <p className="text-sm text-muted">{formatDay(today.now, tz)}</p>
          </div>
          <Button variant="quiet" size="sm" onClick={() => setNote(true)}>
            <NotePencil aria-hidden className="size-5" /> Add a note
          </Button>
        </div>
        <Timeline events={today.events} handled={handled} tz={tz} />
      </section>

      <CheckinSheet open={sheet} onOpenChange={setSheet} />
      <AddNoteSheet open={note} onOpenChange={setNote} />
    </div>
  );
}

/** A notification can arrive before Today has fetched its alert cards. Focus once it exists. */
function AlertFocus({ alerts }: { alerts: Alert[] }) {
  const alertId = useSearchParams().get("alert");
  const focused = useRef<string | null>(null);
  useEffect(() => {
    if (!alertId) {
      focused.current = null;
      return;
    }
    if (focused.current === alertId || !alerts.some((a) => a.id === alertId)) return;
    const card = document.getElementById(`alert-${alertId}`);
    if (!card) return;
    focused.current = alertId;
    card.scrollIntoView({ block: "center" });
    card.focus({ preventScroll: true });
  }, [alertId, alerts]);
  return null;
}

function TodaySkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading today" className="space-y-6">
      <div className="rounded-[var(--radius-card)] border border-line bg-surface p-6">
        <div className="flex items-center gap-6">
          <div className="flex-1 space-y-3">
            <Skeleton className="h-9 w-4/5" />
            <Skeleton className="h-9 w-3/5" />
            <Skeleton className="h-5 w-2/5" />
          </div>
          <Skeleton className="size-40 rounded-full" />
        </div>
        <Skeleton className="mt-6 h-12 w-full" />
      </div>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex gap-4">
          <Skeleton className="h-8 w-16" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-5 w-3/4" />
          </div>
        </div>
      ))}
    </div>
  );
}

function Unreachable({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <div role="alert" className="flex flex-col items-center rounded-[var(--radius-card)] border border-line bg-surface px-6 py-14 text-center">
      <WifiSlash aria-hidden className="size-10 text-amber-ink" weight="duotone" />
      <h1 className="mt-4 font-serif text-2xl font-medium">Couldn&apos;t reach Hearth. Trying again…</h1>
      <p className="mt-2 max-w-sm text-muted">Check-ins carry on as scheduled. This page will update by itself when the connection is back.</p>
      <Button className="mt-6" variant="quiet" onClick={onRetry} disabled={busy}>
        <ArrowClockwise aria-hidden className="size-5" /> {busy ? "Trying…" : "Try now"}
      </Button>
    </div>
  );
}
