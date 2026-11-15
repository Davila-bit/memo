"use client";

import { useState } from "react";
import type { CheckinKind, Cue, ScheduleItem } from "@/lib/contracts";
import { Button } from "../ui/button";
import { Sheet } from "../ui/sheet";

export const FIELD = "mt-2 block min-h-11 w-full rounded-xl border border-line-strong bg-surface px-3 py-2 text-base";
export const GROUPS: { kind: CheckinKind; label: string }[] = [{ kind: "drink", label: "Water" }, { kind: "meal", label: "Meals" }, { kind: "med", label: "Tablets" }];

export function ScheduleEditor({ item, cues, adding, onClose, onSave }: { item: ScheduleItem; cues: Cue[]; adding: boolean; onClose: () => void; onSave: (item: ScheduleItem) => Promise<boolean> }) {
  const [draft, setDraft] = useState(item);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const errorProps = (field: string) => ({ "aria-invalid": errors[field] ? true as const : undefined, "aria-describedby": errors[field] ? `schedule-${field}-error` : undefined });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.time)) next.time = "Choose a time for this check-in.";
    if (!draft.label.trim()) next.label = "Give this check-in a short label.";
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true);
    if (await onSave({ ...draft, label: draft.label.trim() })) onClose();
    setBusy(false);
  }

  return <Sheet open onOpenChange={(open) => { if (!open && !busy) onClose(); }} title={adding ? "Add a check-in" : "Edit check-in"}>
    <form noValidate onSubmit={submit} className="space-y-4">
      <div><label htmlFor="schedule-time" className="font-semibold">Time</label><input id="schedule-time" type="time" className={FIELD} value={draft.time} onChange={(e) => setDraft({ ...draft, time: e.target.value })} {...errorProps("time")} />{errors.time ? <p id="schedule-time-error" role="alert" className="mt-1 text-sm text-coral-ink">{errors.time}</p> : null}</div>
      <div><label htmlFor="schedule-label" className="font-semibold">Label</label><input id="schedule-label" maxLength={100} className={FIELD} value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} {...errorProps("label")} />{errors.label ? <p id="schedule-label-error" role="alert" className="mt-1 text-sm text-coral-ink">{errors.label}</p> : null}</div>
      <div><label htmlFor="schedule-kind" className="font-semibold">Kind of check-in</label><select id="schedule-kind" className={FIELD} value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as CheckinKind, cue_id: null })}>{GROUPS.map(({ kind, label }) => <option key={kind} value={kind}>{label}</option>)}</select></div>
      <div><label htmlFor="schedule-cue" className="font-semibold">Which cue to show</label><select id="schedule-cue" className={FIELD} value={draft.cue_id ?? ""} onChange={(e) => setDraft({ ...draft, cue_id: e.target.value || null })}><option value="">Use the usual cue</option>{cues.filter((cue) => cue.kind === draft.kind).map((cue) => <option key={cue.id} value={cue.id}>{cue.title}</option>)}</select></div>
      <Button type="submit" className="w-full" disabled={busy}>{busy ? "Saving…" : "Save check-in"}</Button>
    </form>
  </Sheet>;
}
