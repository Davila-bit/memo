"use client";

import { CheckCircle, Eye, PhoneCall, PhoneX, Warning, WarningOctagon, VideoCamera, type Icon } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { Checkin, HearthEvent } from "@/lib/contracts";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { confidenceWords, formatTime, isActive, KIND_NOUN, resultTitle } from "@/lib/format";
import { useHearth, type Caption } from "@/lib/store";
import { Button } from "../ui/button";
import { StatusBadge, type Status } from "../ui/status-badge";

const STEPS: { key: "calling" | "answered" | "watching"; label: string; icon: Icon }[] = [
  { key: "calling", label: "Calling", icon: PhoneCall },
  { key: "answered", label: "She answered", icon: VideoCamera },
  { key: "watching", label: "Watching", icon: Eye },
];

function stepIndex(c: Checkin): number {
  if (c.status === "queued" || c.status === "ringing") return 0;
  if (c.status === "connected") return 1;
  return 2;
}

const firstSeen = new Map<string, number>();

/** Seconds since this page first saw the check-in (the demo clock can't skew it). */
function useElapsed(c: Checkin): number {
  const active = isActive(c);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!firstSeen.has(c.id)) firstSeen.set(c.id, Date.now());
    const start = firstSeen.get(c.id) ?? Date.now();
    const update = () => setElapsed(Math.max(0, Math.round((Date.now() - start) / 1000)));
    const first = setTimeout(update, 0);
    const t = active ? setInterval(update, 1000) : null;
    return () => {
      clearTimeout(first);
      if (t) clearInterval(t);
    };
  }, [c.id, active]);
  return elapsed;
}

export function LiveCheckin({ checkin, events }: { checkin: Checkin; events: HearthEvent[] }) {
  const reduce = useReducedMotion();
  const captions = useHearth((s) => s.captions).filter((c) => c.checkin_id === checkin.id);
  const today = useHearth((s) => s.today);
  const dismiss = useHearth((s) => s.dismissResult);
  const setCheckin = useHearth((s) => s.setCheckin);
  const toast = useHearth((s) => s.toast);
  const elapsed = useElapsed(checkin);
  const mum = today?.resident.family_name_for_her ?? "Mum";
  const tz = today?.resident.timezone;
  const active = isActive(checkin);
  const ev = events.find((e) => e.checkin_id === checkin.id && e.kind !== "note");
  const [cancelling, setCancelling] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const finished = !active && (checkin.outcome !== null || checkin.status === "ended");
  useEffect(() => {
    if (finished) useHearth.getState().announce(resultTitle(checkin, ev, mum));
  }, [finished, checkin, ev, mum]);

  const cancel = async () => {
    setCancelling(true);
    try {
      setCheckin(await api.cancelCheckin(checkin.id));
      toast("Check-in ended.", "info");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't end the call.", "warn");
    } finally {
      setCancelling(false);
    }
  };

  const tone = resultTone(checkin);
  const cardTone = active ? "border-sky/40 bg-surface" : TONE_CARD[tone];

  return (
    <motion.section
      aria-labelledby="live-heading"
      data-testid="live-checkin"
      data-status={checkin.status}
      data-outcome={checkin.outcome ?? ""}
      initial={reduce ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn("rounded-[var(--radius-card)] border-2 p-5 shadow-[var(--shadow-card)] sm:p-6", cardTone)}
    >
      {active ? (
        <>
          <div className="flex items-baseline justify-between gap-3">
            <h2 id="live-heading" ref={headingRef} className="font-serif text-2xl font-medium">
              Checking on {mum}: {KIND_NOUN[checkin.kind]}
            </h2>
            <span className="text-sm text-muted tabular" aria-label={`${elapsed} seconds so far`}>
              {elapsed}s
            </span>
          </div>
          <ol className="mt-5 grid grid-cols-3 gap-2" aria-label="Progress">
            {STEPS.map((s, i) => {
              const idx = stepIndex(checkin);
              const state = i < idx ? "done" : i === idx ? "current" : "todo";
              return (
                <li key={s.key} aria-current={state === "current" ? "step" : undefined} className="flex flex-col items-center gap-2 text-center">
                  <span
                    className={cn(
                      "flex size-12 items-center justify-center rounded-full border-2 transition-colors",
                      state === "done" && "border-sage bg-sage-tint text-sage-ink",
                      state === "current" && "border-sky bg-sky-tint text-sky-ink",
                      state === "todo" && "border-line bg-surface text-muted",
                    )}
                  >
                    {state === "done" ? (
                      <CheckCircle aria-hidden className="size-6" weight="fill" />
                    ) : (
                      <s.icon aria-hidden className={cn("size-6", state === "current" && "animate-soft-pulse")} weight="duotone" />
                    )}
                  </span>
                  <span className={cn("text-sm font-semibold", state === "todo" ? "text-muted" : "text-ink")}>
                    {s.label}
                    <span className="sr-only">{state === "done" ? ", done" : state === "current" ? ", now" : ", not yet"}</span>
                  </span>
                </li>
              );
            })}
          </ol>
          <Captions captions={captions} mum={mum} />
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted">Live only. Nothing is recorded.</p>
            <Button variant="quiet" size="sm" onClick={cancel} disabled={cancelling}>
              <PhoneX aria-hidden className="size-5" /> {cancelling ? "Ending…" : "End call"}
            </Button>
          </div>
        </>
      ) : (
        <Result checkin={checkin} ev={ev} mum={mum} tz={tz} tone={tone} onDone={dismiss} />
      )}
    </motion.section>
  );
}

function Captions({ captions, mum }: { captions: Caption[]; mum: string }) {
  const reduce = useReducedMotion();
  return (
    <div className="mt-5 rounded-2xl bg-paper px-4 py-3">
      <div role="log" aria-live="polite" aria-label="Live captions" className="min-h-[4.5rem] space-y-1.5">
        {captions.length === 0 ? (
          <p className="text-[0.9375rem] text-muted">Captions will appear here when the call connects.</p>
        ) : (
          <AnimatePresence initial={false}>
            {captions.slice(-4).map((c) => (
              <motion.p
                key={c.id}
                initial={reduce ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-[1rem] leading-snug"
              >
                <span className={cn("font-semibold", c.speaker === "helper" ? "text-sky-ink" : "text-ink")}>{c.speaker === "helper" ? "Helper" : mum}:</span>{" "}
                {c.text}
              </motion.p>
            ))}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}

type ResultTone = "ok" | "check" | "urgent" | "plain";
const TONE_CARD: Record<ResultTone, string> = {
  ok: "border-sage/50 bg-sage-tint",
  check: "border-amber-fill/60 bg-amber-tint",
  urgent: "border-coral/70 bg-coral-tint",
  plain: "border-line bg-surface",
};

function resultTone(c: Checkin): ResultTone {
  switch (c.outcome) {
    case "confirmed":
      return "ok";
    case "refused":
    case "no_answer":
      return "check";
    case "concern":
      return "urgent";
    default:
      return "plain";
  }
}

function Result({ checkin, ev, mum, tz, tone, onDone }: { checkin: Checkin; ev?: HearthEvent; mum: string; tz?: string; tone: ResultTone; onDone: () => void }) {
  const when = formatTime(ev?.ts ?? checkin.ended_at ?? checkin.created_at, tz);
  const I = tone === "ok" ? CheckCircle : tone === "urgent" ? WarningOctagon : tone === "check" ? Warning : PhoneX;
  const iconCls = tone === "ok" ? "text-sage-ink" : tone === "urgent" ? "text-coral-ink" : tone === "check" ? "text-amber-ink" : "text-muted";
  const status: Status = tone === "ok" ? "ok" : tone === "urgent" ? "urgent" : tone === "check" ? "check" : "waiting";
  const word = tone === "ok" ? "Seen" : tone === "urgent" ? "Needs you now" : tone === "check" ? "Needs a look" : "Ended";
  return (
    <div className="flex gap-4">
      <I aria-hidden weight="fill" className={cn("mt-0.5 size-10 shrink-0 sm:size-12", iconCls)} />
      <div className="min-w-0 flex-1">
        <StatusBadge status={status} size="sm">
          {word}
        </StatusBadge>
        <h2 id="live-heading" className="mt-2 font-serif text-[1.625rem] leading-tight font-medium text-balance sm:text-[1.875rem]">
          {resultTitle(checkin, ev, mum)} <span className="whitespace-nowrap text-muted">· {when}</span>
        </h2>
        {ev?.said ? (
          <p className="mt-2 text-[1.0625rem]">
            She said: <q>{ev.said}</q>
          </p>
        ) : null}
        {ev?.evidence ? (
          <p className="mt-2 text-[0.9375rem] text-muted">
            What the helper saw: {lowerFirst(ev.evidence)}
            {checkin.outcome === "confirmed" ? <span className="text-ink"> · {confidenceWords(ev.confidence)}</span> : null}
          </p>
        ) : null}
        {!ev && checkin.summary ? <p className="mt-2 text-[0.9375rem] text-muted">{checkin.summary}</p> : null}
        <Button variant="quiet" size="sm" className="mt-4" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}

function lowerFirst(s: string): string {
  return s ? s[0].toLowerCase() + s.slice(1) : s;
}
