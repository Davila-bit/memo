"use client";

import { ArrowClockwise, HandHeart, Phone, PhoneCall, UsersThree, Warning, WarningOctagon } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import type { Alert, AlertAction } from "@/lib/contracts";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { alertSentence, formatTime } from "@/lib/format";
import { refreshToday } from "@/lib/live";
import { useHearth } from "@/lib/store";
import { Button, buttonClasses } from "../ui/button";
import { StatusBadge } from "../ui/status-badge";

// TODO(contract): Resident has no phone number, so "I'll call her" can only offer a link when one
// is configured here. Proposed: `Resident.phone: string | null` (E.164).
const MUM_TEL = process.env.NEXT_PUBLIC_HEARTH_MUM_TEL ?? "";

const DONE_TEXT: Record<AlertAction, string> = {
  retry_later: "The helper will try again in 30 minutes.",
  i_will_call: "Marked: you're calling her.",
  ask_neighbour: "Marked: you've asked a neighbour.",
  handling: "Marked: you're handling it.",
  called_911: "Marked: you called 911.",
};

export function AlertCard({ alert }: { alert: Alert }) {
  const reduce = useReducedMotion();
  const today = useHearth((s) => s.today);
  const upsert = useHearth((s) => s.upsertAlert);
  const toast = useHearth((s) => s.toast);
  const [busy, setBusy] = useState<AlertAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mum = today?.resident.family_name_for_her ?? "Mum";
  const tz = today?.resident.timezone;
  const urgent = alert.tier === "urgent";

  const act = async (action: AlertAction, retry_minutes?: number) => {
    setBusy(action);
    setError(null);
    try {
      const a = await api.alertAction(alert.id, { action, retry_minutes: retry_minutes ?? null });
      upsert(a);
      toast(DONE_TEXT[action], "ok");
      void refreshToday();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through. Please try again.");
    } finally {
      setBusy(null);
    }
  };

  const titleId = `alert-${alert.id}-title`;
  return (
    <motion.div
      id={`alert-${alert.id}`}
      tabIndex={-1}
      layout={!reduce}
      initial={reduce ? false : { opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      // Every alert needs Sarah, so each one is announced as it arrives, not only the urgent ones.
      role="alert"
      aria-labelledby={titleId}
      data-testid={`alert-${alert.kind}`}
      className={cn(
        "rounded-[var(--radius-card)] border-2 p-5 shadow-[var(--shadow-card)] sm:p-6",
        urgent ? "border-coral bg-coral-tint" : "border-amber-fill/70 bg-amber-tint",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={urgent ? "urgent" : "check"} icon={urgent ? WarningOctagon : Warning}>
          {urgent ? "Urgent" : "Needs you"}
        </StatusBadge>
        <span className="text-sm text-muted tabular">{formatTime(alert.ts, tz)}</span>
      </div>
      <h2 id={titleId} className="mt-3 font-serif text-[1.625rem] leading-tight font-medium text-balance">
        {urgent ? `${mum} may need you` : alert.title}
      </h2>
      <p className="mt-2 text-[1.0625rem]">{alertSentence(alert, tz)}</p>
      {alert.said && !urgent && !alert.detail.includes(alert.said) ? (
        <p className="mt-2 text-[0.9375rem] text-muted">
          In her words: <q className="text-ink">{alert.said}</q>
        </p>
      ) : null}

      {urgent ? (
        <div className="mt-5 flex flex-col gap-2.5 sm:flex-row">
          <a
            href="tel:911"
            onClick={() => void act("called_911")}
            className={buttonClasses("coral", "lg", "sm:flex-1")}
          >
            <Phone aria-hidden className="size-5" weight="fill" /> Call 911
          </a>
          <Button variant="quiet" size="lg" className="sm:flex-1" disabled={busy !== null} onClick={() => act("handling")}>
            <HandHeart aria-hidden className="size-5" /> {busy === "handling" ? "Saving…" : "I'm handling it"}
          </Button>
        </div>
      ) : (
        <div className="mt-5 flex flex-col gap-2.5 sm:flex-row sm:flex-wrap">
          <Button variant="primary" disabled={busy !== null} onClick={() => act("retry_later", 30)}>
            <ArrowClockwise aria-hidden className="size-5" /> {busy === "retry_later" ? "Saving…" : "Try again in 30 min"}
          </Button>
          {MUM_TEL ? (
            <a href={`tel:${MUM_TEL}`} onClick={() => void act("i_will_call")} className={buttonClasses("quiet")}>
              <PhoneCall aria-hidden className="size-5" /> I&apos;ll call her
            </a>
          ) : (
            <Button variant="quiet" disabled={busy !== null} onClick={() => act("i_will_call")}>
              <PhoneCall aria-hidden className="size-5" /> {busy === "i_will_call" ? "Saving…" : "I'll call her"}
            </Button>
          )}
          <Button variant="quiet" disabled={busy !== null} onClick={() => act("ask_neighbour")}>
            <UsersThree aria-hidden className="size-5" /> {busy === "ask_neighbour" ? "Saving…" : "Ask a neighbour"}
          </Button>
        </div>
      )}
      {urgent ? (
        <p className="mt-3 text-sm text-muted">The helper never calls 911 itself. It has told {mum} it is getting you.</p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-3 text-[0.9375rem] font-medium text-coral-ink">
          {error}
        </p>
      ) : null}
    </motion.div>
  );
}
