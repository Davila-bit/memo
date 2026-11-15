"use client";

import { CheckCircle, Circle, Clock, ForkKnife, Pill } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import type { ScheduleItem, Today } from "@/lib/contracts";
import { formatHHMM, hydrationHeadline, hydrationSub, nextLine, slotsFor, type MedSlot } from "@/lib/format";
import { HydrationRing } from "./hydration-ring";

export function TodaySummary({ today, schedule, actions }: { today: Today; schedule: ScheduleItem[] | null; actions: ReactNode }) {
  const mum = today.resident.family_name_for_her;
  const meds = slotsFor(today, schedule, "med");
  const meals = slotsFor(today, schedule, "meal");
  const next = nextLine(today);
  return (
    <section aria-labelledby="today-heading" className="rounded-[var(--radius-card)] border border-line bg-surface p-5 shadow-[var(--shadow-card)] sm:p-7">
      <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:gap-8">
        <div className="min-w-0 flex-1">
          <h1 id="today-heading" className="font-serif text-[2rem] leading-[1.12] font-medium tracking-tight text-balance sm:text-[2.5rem]">
            {hydrationHeadline(today, mum)}
          </h1>
          <p className="mt-2 hidden text-[1.0625rem] text-muted sm:block">{hydrationSub(today)}</p>
        </div>
        <div className="flex items-center gap-5">
          <HydrationRing ml={today.fluid_ml} target={today.fluid_target_ml} mum={mum} />
          <p className="text-[1.0625rem] text-muted sm:hidden">{hydrationSub(today)}</p>
        </div>
      </div>

      <dl className="mt-6 grid gap-3 border-t border-line pt-5 sm:grid-cols-2 sm:gap-6">
        <SlotRow icon={<Pill aria-hidden className="size-5" weight="duotone" />} term="Tablets" slots={meds} doneWord="taken" />
        <SlotRow icon={<ForkKnife aria-hidden className="size-5" weight="duotone" />} term="Meals" slots={meals} doneWord="eaten" />
      </dl>

      <div className="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        {next ? (
          <p className="flex items-center gap-2 text-[0.9375rem] text-muted">
            <Clock aria-hidden className="size-5 shrink-0" weight="duotone" />
            {next}
          </p>
        ) : (
          <span />
        )}
        {actions}
      </div>
    </section>
  );
}

function SlotRow({ icon, term, slots, doneWord }: { icon: ReactNode; term: string; slots: MedSlot[]; doneWord: string }) {
  return (
    <div className="flex items-start gap-3">
      <dt className="flex min-w-[5.5rem] items-center gap-2 pt-0.5 font-semibold text-ink">
        {icon}
        {term}
      </dt>
      <dd className="min-w-0 flex-1">
        {slots.length === 0 ? (
          <span className="text-muted">None scheduled</span>
        ) : (
          <ul className="flex flex-wrap gap-x-3 gap-y-1">
            {slots.map((s, i) => (
              <li key={`${s.label}-${i}`} className="flex items-center gap-1.5 text-[0.9375rem]">
                {s.done ? (
                  <>
                    <CheckCircle aria-hidden className="size-5 text-sage-ink" weight="fill" />
                    <span>
                      {s.label} <span className="text-sage-ink">{doneWord}</span>
                    </span>
                  </>
                ) : (
                  <>
                    <Circle aria-hidden className="size-5 text-muted" />
                    <span className="text-muted">
                      {s.label}
                      {s.time ? ` due ${formatHHMM(s.time)}` : " to come"}
                    </span>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </dd>
    </div>
  );
}
