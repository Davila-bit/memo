"use client";

import { CheckCircle } from "@phosphor-icons/react";
import type { DayStats } from "@/lib/contracts";
import { cn } from "@/lib/cn";
import { dayLong, formatMl, hasActivity, weekdayShort } from "./week-words";

/**
 * Seven bars against her goal line. Every bar shows its day and ml as text; each column is a
 * keyboard stop whose accessible text is the full sentence, so the picture never relies on colour
 * or shape alone.
 */
export function FluidChart({ days, target }: { days: DayStats[]; target: number }) {
  // Headroom above the tallest bar (or the goal) for its value label and tick.
  const max = Math.max(target, ...days.map((d) => d.fluid_ml), 1) * 1.3;
  const goalPct = (target / max) * 100;
  const last = days.length - 1;

  return (
    <section
      aria-labelledby="fluids-heading"
      className="rounded-[var(--radius-card)] border border-line bg-surface p-5 shadow-[var(--shadow-card)] sm:p-7"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="fluids-heading" className="font-serif text-[1.5rem] leading-tight font-medium">
          Fluids per day
        </h2>
        <p className="flex items-center gap-2 text-sm text-muted">
          <span aria-hidden className="inline-block w-6 border-t-2 border-dashed border-ink/55" />
          Goal {formatMl(target)} ml
        </p>
      </div>

      <ol aria-label={`Fluids per day, against her ${formatMl(target)} ml goal`} data-testid="fluid-bars" className="mt-6 grid h-60 grid-cols-7 sm:h-72">
        {days.map((d, i) => {
          const isToday = i === last;
          const pct = (d.fluid_ml / max) * 100;
          const met = d.fluid_ml >= target;
          const quiet = !hasActivity(d);
          const said = `${isToday ? "Today, " : ""}${dayLong(d.day)}: ${
            quiet ? "no check-ins" : `${formatMl(d.fluid_ml)} ml${isToday ? " so far" : ""}`
          }${quiet ? "" : met ? ", goal reached" : `, ${formatMl(target - d.fluid_ml)} ml under her goal`}.`;
          return (
            <li
              key={d.day}
              tabIndex={0}
              data-today={isToday || undefined}
              className={cn("flex h-full flex-col rounded-2xl px-1 pt-1 pb-2 sm:px-2", isToday && "bg-sage-tint")}
            >
              <span className="sr-only">{said}</span>
              <div aria-hidden className="relative flex-1 border-b border-line-strong/60">
                <div className="absolute inset-x-[-0.25rem] border-t-2 border-dashed border-ink/40 sm:inset-x-[-0.5rem]" style={{ bottom: `${goalPct}%` }} />
                <div
                  data-bar
                  className={cn(
                    "absolute inset-x-1 bottom-0 rounded-t-lg transition-[height] duration-500 sm:inset-x-2",
                    isToday ? "bg-sage" : "bg-sky",
                  )}
                  style={{ height: `${pct}%` }}
                />
                <div
                  className="absolute inset-x-0 flex flex-col items-center text-center leading-tight"
                  style={{ bottom: `calc(${pct}% + 0.25rem)` }}
                >
                  {met ? <CheckCircle className="size-4 text-sage-ink" weight="fill" /> : null}
                  <span className={cn("text-[0.8125rem] font-semibold tabular-nums sm:text-sm", quiet ? "text-muted" : "text-ink")}>
                    {quiet ? "–" : formatMl(d.fluid_ml)}
                    {quiet ? null : <span className="hidden font-normal text-muted sm:inline"> ml</span>}
                  </span>
                </div>
              </div>
              <div aria-hidden className="pt-2 text-center leading-tight">
                <span className={cn("block text-[0.8125rem] sm:text-sm", isToday ? "font-semibold text-sage-ink" : "text-muted")}>
                  {isToday ? "Today" : weekdayShort(d.day)}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 text-sm text-muted sm:hidden">Amounts in ml.</p>
    </section>
  );
}
