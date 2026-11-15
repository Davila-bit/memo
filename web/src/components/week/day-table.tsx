"use client";

import { CheckCircle, Circle, HandPalm, PhoneSlash, Warning } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import type { DayStats } from "@/lib/contracts";
import { dayLong, dayShort, formatMl, hasActivity, parseDay, weekdayShort } from "./week-words";

/** The same seven days in words: one row per day. Doubles as the chart's text alternative. */
export function DayTable({ days }: { days: DayStats[] }) {
  const last = days.length - 1;
  // Newest first reads naturally in a list ("today, yesterday, …").
  const rows = days.map((d, i) => ({ d, isToday: i === last, isYesterday: i === last - 1 })).reverse();
  return (
    <section
      aria-labelledby="days-heading"
      className="rounded-[var(--radius-card)] border border-line bg-surface p-5 shadow-[var(--shadow-card)] sm:p-7"
    >
      <h2 id="days-heading" className="font-serif text-[1.5rem] leading-tight font-medium">
        Day by day
      </h2>
      <table data-testid="day-table" className="mt-4 w-full border-collapse text-left text-sm sm:text-[0.9375rem]">
        <caption className="sr-only">Fluids, drinks, tablets and missed check-ins for each of the last seven days, newest first.</caption>
        <thead>
          <tr className="border-b border-line text-[0.8125rem] font-semibold tracking-[0.06em] text-muted uppercase">
            <th scope="col" className="py-2 pr-2 font-semibold">Day</th>
            <th scope="col" className="hidden py-2 pr-2 font-semibold sm:table-cell">Fluids</th>
            <th scope="col" className="py-2 pr-2 font-semibold">Drinks</th>
            <th scope="col" className="py-2 pr-2 font-semibold">Tablets</th>
            <th scope="col" className="py-2 font-semibold">Missed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ d, isToday, isYesterday }) => (
            <tr key={d.day} className="border-b border-line align-top last:border-b-0">
              <th scope="row" className="py-3 pr-2 font-semibold">
                {isToday || isYesterday ? (
                  <>
                    {isToday ? "Today" : "Yesterday"}
                    <span className="sr-only">, {dayLong(d.day)}</span>
                  </>
                ) : (
                  <>
                    <span aria-hidden className="whitespace-nowrap sm:hidden">
                      {weekdayShort(d.day)} {parseDay(d.day).getDate()}
                    </span>
                    <span aria-hidden className="hidden whitespace-nowrap sm:inline">
                      {dayShort(d.day)}
                    </span>
                    <span className="sr-only">{dayLong(d.day)}</span>
                  </>
                )}
              </th>
              {hasActivity(d) ? (
                <>
                  <td className="hidden py-3 pr-2 tabular-nums sm:table-cell">
                    {formatMl(d.fluid_ml)} ml{isToday ? <span className="text-muted"> so far</span> : null}
                  </td>
                  <td className="py-3 pr-2">
                    <Drinks d={d} />
                  </td>
                  <td className="py-3 pr-2">
                    <Tablets d={d} isToday={isToday} />
                  </td>
                  <td className="py-3">
                    <Missed n={d.checkins_missed} />
                  </td>
                </>
              ) : (
                <td colSpan={4} className="py-3 text-muted">
                  No check-ins this day
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Line({ icon, children, className }: { icon: ReactNode; children: ReactNode; className?: string }) {
  return (
    <span className={`flex items-start gap-1.5 ${className ?? ""}`}>
      <span className="flex h-[1.4em] items-center">{icon}</span>
      <span className="whitespace-nowrap">{children}</span>
    </span>
  );
}

function Drinks({ d }: { d: DayStats }) {
  if (d.drinks_confirmed === 0 && d.drinks_refused === 0) return <span className="text-muted">None</span>;
  return (
    <span className="flex flex-col gap-1">
      {d.drinks_confirmed > 0 ? (
        <Line icon={<CheckCircle aria-hidden className="size-4 shrink-0 text-sage-ink" weight="fill" />}>{d.drinks_confirmed} had</Line>
      ) : null}
      {d.drinks_refused > 0 ? (
        <Line icon={<HandPalm aria-hidden className="size-4 shrink-0 text-amber-ink" weight="fill" />} className="text-amber-ink">
          {d.drinks_refused} said no
        </Line>
      ) : null}
    </span>
  );
}

function Tablets({ d, isToday }: { d: DayStats; isToday: boolean }) {
  if (d.meds_expected === 0) return <span className="text-muted">None due</span>;
  const label = `${d.meds_confirmed} of ${d.meds_expected}`;
  if (d.meds_confirmed >= d.meds_expected) {
    return <Line icon={<CheckCircle aria-hidden className="size-4 shrink-0 text-sage-ink" weight="fill" />}>{label}</Line>;
  }
  if (isToday) {
    return (
      <Line icon={<Circle aria-hidden className="size-4 shrink-0 text-muted" />}>
        {label}
        <span className="block text-muted sm:inline"> so far</span>
      </Line>
    );
  }
  return (
    <Line icon={<Warning aria-hidden className="size-4 shrink-0 text-amber-ink" weight="fill" />} className="text-amber-ink">
      {label}
    </Line>
  );
}

function Missed({ n }: { n: number }) {
  if (n === 0) return <span className="text-muted">None</span>;
  return (
    <Line icon={<PhoneSlash aria-hidden className="size-4 shrink-0 text-amber-ink" weight="fill" />} className="text-amber-ink">
      {n} missed
    </Line>
  );
}
