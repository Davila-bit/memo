"use client";

import { ArrowClockwise, ChartBar, Drop, Pill, PhoneSlash, WifiSlash } from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import type { Week } from "@/lib/contracts";
import { useHearth } from "@/lib/store";
import { PageHeader } from "../page-header";
import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { StatusBadge } from "../ui/status-badge";
import { DayTable } from "./day-table";
import { FluidChart } from "./fluid-chart";
import { hasActivity, sortDays, streakLine, weekHeadline, weekSub, weekTotals } from "./week-words";

export function WeekView() {
  const mum = useHearth((s) => s.me?.resident.family_name_for_her) ?? "Mum";
  // Refetch when a check-in lands on Today (live channel), so the week never lags the timeline.
  const liveTick = useHearth((s) => s.today?.events.length ?? 0);
  const [week, setWeek] = useState<Week | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .week()
      .then((w) => {
        if (cancelled) return;
        setWeek(w);
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        console.warn("[week] load failed", e);
        setError(e instanceof ApiError ? e.message : "Couldn't reach Memo.");
      });
    return () => {
      cancelled = true;
    };
  }, [attempt, liveTick]);

  const retry = () => {
    setError(null);
    setWeek(null);
    setAttempt((n) => n + 1);
  };

  if (!week) return error ? <WeekError message={error} onRetry={retry} /> : <WeekSkeleton />;

  const days = sortDays(week.days);
  if (!days.some(hasActivity)) return <WeekEmpty />;

  const totals = weekTotals(days);
  const streak = streakLine(week.med_streak_days);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-[2rem] leading-[1.12] font-medium tracking-tight text-balance sm:text-[2.5rem]">
          {weekHeadline(days, mum)}
        </h1>
        <p className="mt-2 max-w-prose text-[1.0625rem] text-muted">{weekSub(days, week.fluid_target_ml)}</p>
      </header>

      <FluidChart days={days} target={week.fluid_target_ml} />

      <section aria-labelledby="totals-heading">
        <h2 id="totals-heading" className="sr-only">
          The week in totals
        </h2>
        <dl className="grid gap-3 sm:grid-cols-3 sm:gap-4">
          <Tile icon={<Drop aria-hidden className="size-5" weight="duotone" />} term="Drinks">
            <span className="text-ink">{totals.drinks} had</span>
            {totals.refused > 0 ? <span className="text-muted"> · said no to {totals.refused}</span> : null}
          </Tile>
          <Tile icon={<Pill aria-hidden className="size-5" weight="duotone" />} term="Tablets">
            <span className="text-ink">
              {totals.medsTaken} of {totals.medsExpected} observed
            </span>
            {streak ? (
              <span className="mt-2 block">
                <StatusBadge status="ok" size="sm">
                  {streak}
                </StatusBadge>
              </span>
            ) : null}
          </Tile>
          <Tile icon={<PhoneSlash aria-hidden className="size-5" weight="duotone" />} term="Missed check-ins">
            {totals.missed === 0 ? (
              <span className="text-ink">None</span>
            ) : (
              <span className="text-amber-ink">
                {totals.missed === 1 ? "1 call" : `${totals.missed} calls`} she didn&apos;t answer
              </span>
            )}
          </Tile>
        </dl>
      </section>

      <DayTable days={days} />

      {week.source === "tiger_continuous_aggregate" ? (
        <p className="text-sm text-muted">Daily totals come from Tiger Data continuous aggregates.</p>
      ) : null}
    </div>
  );
}

function Tile({ icon, term, children }: { icon: ReactNode; term: string; children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-line bg-surface px-4 py-3.5">
      <dt className="flex items-center gap-2 text-[0.8125rem] font-semibold tracking-[0.06em] text-muted uppercase">
        {icon}
        {term}
      </dt>
      <dd className="mt-1.5 text-[1.0625rem] font-medium">{children}</dd>
    </div>
  );
}

const INTRO = `Seven days at a glance: how much she drank, tablets and any missed check-ins.`;

function WeekSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading the week" className="space-y-6">
      <div className="space-y-3">
        <Skeleton className="h-10 w-4/5" />
        <Skeleton className="h-5 w-3/5" />
      </div>
      <div className="rounded-[var(--radius-card)] border border-line bg-surface p-5 sm:p-7">
        <Skeleton className="h-7 w-40" />
        <div className="mt-6 grid h-60 grid-cols-7 items-end gap-2 sm:h-72">
          {[45, 70, 55, 85, 60, 75, 40].map((h, i) => (
            <div key={i} aria-hidden className="skeleton rounded-t-xl" style={{ height: `${h}%` }} />
          ))}
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
    </div>
  );
}

function WeekEmpty() {
  return (
    <>
      <PageHeader title="Week" intro={INTRO} />
      <div className="flex flex-col items-center rounded-[var(--radius-card)] border border-line bg-surface px-6 py-14 text-center">
        <ChartBar aria-hidden className="size-10 text-sage-ink" weight="duotone" />
        <p className="mt-4 font-serif text-2xl font-medium">No check-ins yet this week</p>
        <p className="mt-2 max-w-sm text-muted">Once the helper has checked in, each day&apos;s drinks and tablets will show up here.</p>
      </div>
    </>
  );
}

function WeekError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <>
      <PageHeader title="Week" intro={INTRO} />
      <div role="alert" className="flex flex-col items-center rounded-[var(--radius-card)] border border-line bg-surface px-6 py-14 text-center">
        <WifiSlash aria-hidden className="size-10 text-amber-ink" weight="duotone" />
        <p className="mt-4 font-serif text-2xl font-medium">Couldn&apos;t load the week.</p>
        <p className="mt-2 max-w-sm text-muted">{message} Check-ins carry on as scheduled.</p>
        <Button className="mt-6" variant="quiet" onClick={onRetry}>
          <ArrowClockwise aria-hidden className="size-5" /> Try again
        </Button>
      </div>
    </>
  );
}
