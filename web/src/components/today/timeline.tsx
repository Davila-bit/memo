"use client";

import { CaretDown, Drop, Ear, ForkKnife, HandPalm, NotePencil, Pill, PhoneX, WarningOctagon, type Icon } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import type { Alert, AlertAction, EventKind, HearthEvent } from "@/lib/contracts";
import { cn } from "@/lib/cn";
import { confidenceWords, eventLabel, formatTime, isReportedEvent, SOURCE_LABEL, timeParts } from "@/lib/format";

export const EVENT_STYLE: Record<EventKind, { icon: Icon; badge: string; dot: string }> = {
  drink: { icon: Drop, badge: "bg-sky-tint text-sky-ink", dot: "bg-sky" },
  meal: { icon: ForkKnife, badge: "bg-sage-tint text-sage-ink", dot: "bg-sage" },
  med: { icon: Pill, badge: "bg-sage-tint text-sage-ink", dot: "bg-sage" },
  refused: { icon: HandPalm, badge: "bg-amber-tint text-amber-ink", dot: "bg-amber-fill" },
  missed: { icon: PhoneX, badge: "bg-amber-tint text-amber-ink", dot: "bg-amber-fill" },
  concern: { icon: WarningOctagon, badge: "bg-coral-tint text-coral-ink", dot: "bg-coral" },
  note: { icon: NotePencil, badge: "bg-paper text-muted border border-line", dot: "bg-line-strong" },
};

export function eventStyle(ev: HearthEvent) {
  return isReportedEvent(ev)
    ? { icon: Ear, badge: "bg-sky-tint text-sky-ink", dot: "bg-sky" }
    : EVENT_STYLE[ev.kind];
}

const ACTION_TEXT: Record<AlertAction, string> = {
  retry_later: "You asked the helper to try again later",
  i_will_call: "You called her",
  ask_neighbour: "You asked a neighbour",
  handling: "You handled it",
  called_911: "You called 911",
};

export function Timeline({ events, handled, tz }: { events: HearthEvent[]; handled: Alert[]; tz?: string }) {
  const reduce = useReducedMotion();
  if (events.length === 0) {
    return (
      <div className="rounded-[var(--radius-card)] border border-dashed border-line-strong/50 bg-surface/60 px-6 py-10 text-center text-muted">
        Nothing logged yet today. The first check-in will show up here.
      </div>
    );
  }
  return (
    <ol className="relative" data-testid="timeline">
      <AnimatePresence initial={false}>
        {events.map((e, i) => (
          <motion.li
            key={e.id}
            layout={!reduce}
            initial={reduce ? false : { opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            className="relative"
          >
            <Item ev={e} tz={tz} last={i === events.length - 1} handled={handled.find((a) => a.event_id === e.id)} />
          </motion.li>
        ))}
      </AnimatePresence>
    </ol>
  );
}

function Item({ ev, tz, last, handled }: { ev: HearthEvent; tz?: string; last: boolean; handled?: Alert }) {
  const [open, setOpen] = useState(false);
  const reported = isReportedEvent(ev);
  const s = eventStyle(ev);
  const t = timeParts(ev.ts, tz);
  const hasMore = Boolean(ev.evidence || ev.said || (ev.med_steps && ev.med_steps.length));
  const detailsId = `ev-${ev.id}-details`;
  return (
    <div className="grid grid-cols-[4.25rem_1.25rem_1fr] gap-x-2 sm:grid-cols-[5.5rem_1.5rem_1fr] sm:gap-x-3">
      <div className="pt-0.5 text-right">
        <span className="font-serif text-[1.625rem] leading-none font-medium tabular sm:text-[1.875rem]">{t.clock}</span>
        <span className="block text-[0.8125rem] text-muted">{t.period}</span>
      </div>
      <div className="relative flex justify-center" aria-hidden>
        <span className={cn("relative z-10 mt-2.5 size-3 rounded-full ring-4 ring-paper", s.dot)} />
        {!last ? <span className="absolute top-5 bottom-0 w-px bg-line" /> : null}
      </div>
      <div className="min-w-0 pb-7">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[0.8125rem] font-semibold", s.badge)}>
            <s.icon aria-hidden className="size-4" weight="bold" />
            {eventLabel(ev)}
          </span>
          <span className="inline-flex items-center rounded-full border border-line px-2 py-0.5 text-[0.75rem] font-medium text-muted">
            <span className="sr-only">Source: </span>
            {SOURCE_LABEL[ev.source]}
          </span>
        </div>
        <p className="mt-1.5 text-[1.0625rem] leading-snug text-ink">{ev.summary}</p>
        {handled?.action ? (
          <p className="mt-1 text-[0.9375rem] text-sage-ink">
            {ACTION_TEXT[handled.action]}
            {handled.action_at ? ` · ${formatTime(handled.action_at, tz)}` : ""}
          </p>
        ) : null}
        {hasMore ? (
          <>
            <button
              type="button"
              aria-expanded={open}
              aria-controls={detailsId}
              onClick={() => setOpen((o) => !o)}
              className="-ml-2 mt-1 inline-flex min-h-11 items-center gap-1 rounded-full px-2 text-[0.9375rem] font-medium text-sky-ink hover:bg-sky-tint"
            >
              {open ? "Hide details" : reported ? "What the helper heard" : "What the helper saw"}
              <CaretDown aria-hidden className={cn("size-4 transition-transform", open && "rotate-180")} weight="bold" />
            </button>
            <div id={detailsId} hidden={!open} className="mt-1 space-y-1 rounded-xl bg-surface px-3 py-2.5 text-[0.9375rem] text-muted">
              {ev.evidence ? <p>{ev.evidence}</p> : null}
              {ev.said ? (
                <p>
                  She said: <q className="text-ink">{ev.said}</q>
                </p>
              ) : null}
              {ev.source !== "manual" && (ev.kind === "drink" || ev.kind === "meal" || ev.kind === "med") ? (
                <p className="text-ink">{confidenceWords(ev.confidence)}</p>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
