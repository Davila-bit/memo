"use client";

import { Check, Minus, Plus } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Cue, ScheduleItem } from "@/lib/contracts";
import { formatHHMM } from "@/lib/format";
import { useHearth } from "@/lib/store";
import { PageHeader } from "../page-header";
import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { Sheet } from "../ui/sheet";
import { Skeleton } from "../ui/skeleton";
import { CueCard } from "./cue-card";
import { GROUPS, ScheduleEditor } from "./schedule-editor";

export function ScheduleView() {
  const me = useHearth((s) => s.me);
  const lastNotice = useRef<number | null>(null);
  const [items, setItems] = useState<ScheduleItem[] | null>(null);
  const [cues, setCues] = useState<Cue[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [editor, setEditor] = useState<ScheduleItem | null>(null);
  const [deleting, setDeleting] = useState<ScheduleItem | null>(null);
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const mum = me?.resident.family_name_for_her ?? "Mum";

  function toast(text: string, tone: "ok" | "warn") {
    const state = useHearth.getState();
    if (lastNotice.current !== null) state.dropToast(lastNotice.current);
    state.toast(text, tone);
    lastNotice.current = useHearth.getState().toasts.at(-1)?.id ?? null;
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.schedule(), api.cues()]).then(([schedule, library]) => {
      if (cancelled) return;
      setItems(schedule);
      setCues(library);
      setError(null);
    }).catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load the schedule."); });
    return () => { cancelled = true; };
  }, [attempt]);

  function replace(next: ScheduleItem[]) {
    setItems(next);
    useHearth.getState().setSchedule(next);
  }

  async function mutate(item: ScheduleItem, action: "save" | "delete"): Promise<boolean> {
    if (!items || saving.current) return false;
    saving.current = true;
    setBusy(true);
    const before = items;
    const adding = !before.some((s) => s.id === item.id);
    replace(action === "delete" ? before.filter((s) => s.id !== item.id) : adding ? [...before, item] : before.map((s) => s.id === item.id ? item : s));
    try {
      if (action === "delete") await api.deleteSchedule(item.id);
      else {
        const saved = adding ? await api.createSchedule({ ...item, id: undefined }) : await api.updateSchedule(item.id, item);
        replace((adding ? [...before, saved] : before.map((s) => s.id === item.id ? saved : s)));
      }
      toast(action === "delete" ? "Check-in removed." : "Saved.", "ok");
      return true;
    } catch (err) {
      replace(before);
      toast(err instanceof Error ? err.message : "Couldn't save that change. Please try again.", "warn");
      return false;
    } finally { saving.current = false; setBusy(false); }
  }

  return <>
    <PageHeader title="A familiar rhythm" intro={`Little check-ins, at the times that suit ${mum}.`} />
    {error ? <Card tone="check"><p role="alert">{error}</p><Button variant="quiet" className="mt-3" onClick={() => setAttempt((n) => n + 1)}>Try again</Button></Card> : !items ? <div aria-busy="true" aria-label="Loading schedule" className="space-y-4"><Skeleton className="h-32" /><Skeleton className="h-64" /></div> : <>
      <section aria-labelledby="checkin-times">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 id="checkin-times" className="font-serif text-2xl">Check-in times</h2><Button variant="sage" disabled={busy || !me} onClick={() => setEditor({ id: "new", resident_id: me!.resident.id, kind: "drink", time: "15:00", label: "", cue_id: null, enabled: true })}><Plus aria-hidden className="size-5" />Add a check-in</Button></div>
        <div className="space-y-4">{GROUPS.map(({ kind, label }) => <Card key={kind} aria-labelledby={`group-${kind}`}>
          <h3 id={`group-${kind}`} className="font-serif text-xl">{label}</h3>
          <ul className="mt-2 divide-y divide-line">{items.filter((s) => s.kind === kind).sort((a, b) => a.time.localeCompare(b.time)).map((item) => <li key={item.id} className="py-3" data-testid={`schedule-${item.id}`}>
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1"><p className="font-serif text-2xl tabular">{formatHHMM(item.time)}</p><p className="mt-1 break-words text-sm text-muted">{item.label}</p></div>
              <button type="button" role="switch" aria-label={`${item.label} check-in`} aria-checked={item.enabled} disabled={busy} onClick={() => void mutate({ ...item, enabled: !item.enabled }, "save")} className={`inline-flex min-h-11 min-w-20 shrink-0 items-center justify-center gap-1.5 rounded-full border px-3 text-sm font-semibold disabled:opacity-60 ${item.enabled ? "border-sage/40 bg-sage-tint text-sage-ink" : "border-line-strong bg-surface text-muted"}`}>{item.enabled ? <Check aria-hidden className="size-4" /> : <Minus aria-hidden className="size-4" />}{item.enabled ? "On" : "Off"}</button>
            </div>
            <div className="mt-1 flex gap-1"><Button variant="ghost" size="sm" className="-ml-3" aria-label={`Edit ${item.label}`} disabled={busy} onClick={() => setEditor(item)}>Edit</Button><Button variant="ghost" size="sm" className="text-coral-ink" aria-label={`Delete ${item.label}`} disabled={busy} onClick={() => setDeleting(item)}>Delete</Button></div>
          </li>)}</ul>
          {!items.some((s) => s.kind === kind) ? <p className="mt-3 text-sm text-muted">No check-ins yet.</p> : null}
        </Card>)}</div>
        {me ? <p className="mt-4 text-sm text-muted">Quiet hours: {formatHHMM(me.resident.quiet_hours[0])} to {formatHHMM(me.resident.quiet_hours[1])}. No scheduled check-ins during this time.</p> : null}
      </section>
      <section className="mt-10" aria-labelledby="cue-library"><h2 id="cue-library" className="font-serif text-2xl">What the helper shows {mum}</h2><p className="mt-2 text-muted">The helper shows {mum} her own mug, plate and pill box so the request makes sense to her.</p>
        <div className="mt-5 grid gap-4 sm:grid-cols-2">{cues.map((cue) => <CueCard key={cue.id} cue={cue} notify={toast} onChange={(next) => setCues((old) => old.map((c) => c.id === next.id ? next : c))} />)}</div>
        {!cues.length ? <p className="mt-4 text-muted">No cues have been added yet.</p> : null}
      </section>
    </>}
    {editor ? <ScheduleEditor item={editor} cues={cues} adding={editor.id === "new"} onClose={() => setEditor(null)} onSave={(item) => mutate(item, "save")} /> : null}
    <Sheet open={!!deleting} onOpenChange={(open) => { if (!open && !busy) setDeleting(null); }} title="Remove this check-in?" description={deleting ? `${deleting.label} at ${formatHHMM(deleting.time)} will be removed from the schedule.` : undefined}>
      <p className="text-muted">You can add it again whenever you like.</p><div className="mt-5 flex flex-wrap gap-2"><Button variant="quiet" disabled={busy} onClick={() => setDeleting(null)}>Keep check-in</Button><Button variant="coral" disabled={busy} onClick={async () => { if (deleting && await mutate(deleting, "delete")) setDeleting(null); }}>{busy ? "Removing…" : "Remove check-in"}</Button></div>
    </Sheet>
  </>;
}
