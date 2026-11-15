"use client";

import { ArrowCounterClockwise, Clock, FastForward } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { demoEnabled } from "@/lib/env";
import { formatTime } from "@/lib/format";
import { refreshToday } from "@/lib/live";
import { useHearth } from "@/lib/store";
import { Button } from "./ui/button";
import { Sheet } from "./ui/sheet";

/** Demo clock (development or ?demo=1): shows the app's time and moves it forward. */
export function DemoClock() {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const today = useHearth((s) => s.today);
  const toast = useHearth((s) => s.toast);
  const setToday = useHearth((s) => s.setToday);
  const setCheckin = useHearth((s) => s.setCheckin);
  const [, tick] = useState(0);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reads window/sessionStorage, client only
    setEnabled(demoEnabled());
    const t = setInterval(() => tick((x) => x + 1), 15_000);
    return () => clearInterval(t);
  }, []);

  if (!enabled || !today) return null;
  const tz = today.resident.timezone;
  const label = formatTime(today.now, tz);

  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true);
    try {
      await fn();
      await refreshToday();
      toast(msg, "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "That didn't work.", "warn");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-dashed border-line-strong/70 bg-surface px-3 text-sm font-medium text-ink hover:border-line-strong"
        aria-label={`Demo clock: ${label}. Change the time`}
      >
        <Clock aria-hidden className="size-4.5" weight="duotone" />
        <span className="tabular">{label}</span>
      </button>
      <Sheet open={open} onOpenChange={setOpen} title="Demo clock" description="Moves Memo's time so a scheduled check-in comes round on cue. Only shown for demos.">
        <p className="font-serif text-5xl tabular">{label}</p>
        <p className="mt-1 text-sm text-muted">{today.next_checkin ? `Next scheduled: ${today.next_checkin.label.toLowerCase()}` : "Nothing else scheduled today."}</p>
        <div className="mt-5 grid grid-cols-2 gap-3">
          <Button variant="quiet" disabled={busy} onClick={() => run(() => api.setClock({ advance_minutes: 30 }), "Moved on 30 minutes.")}>
            <FastForward aria-hidden className="size-5" /> +30 min
          </Button>
          <Button variant="quiet" disabled={busy} onClick={() => run(() => api.setClock({ advance_minutes: 60 }), "Moved on an hour.")}>
            <FastForward aria-hidden className="size-5" weight="fill" /> +1 h
          </Button>
          <Button variant="quiet" disabled={busy} onClick={() => run(() => api.setClock({ reset: true }), "Clock is back to now.")}>
            <ArrowCounterClockwise aria-hidden className="size-5" /> Reset
          </Button>
          <Button
            variant="quiet"
            disabled={busy}
            onClick={() =>
              run(async () => {
                const t = await api.resetDemo();
                setCheckin(null);
                setToday(t);
              }, "Demo data is fresh again.")
            }
          >
            <ArrowCounterClockwise aria-hidden className="size-5" weight="bold" /> Reset data
          </Button>
        </div>
      </Sheet>
    </>
  );
}
