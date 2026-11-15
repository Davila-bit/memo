"use client";

import { CheckCircle, Warning, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Resident, Watch, WatchRequest } from "@/lib/contracts";
import { formatTime } from "@/lib/format";
import { Button } from "../ui/button";

const statuses = {
  ok: { Icon: CheckCircle, label: "OK", color: "text-sage-ink" },
  unusual: { Icon: Warning, label: "Unusual", color: "text-amber-ink" },
  concern: { Icon: WarningCircle, label: "Needs you", color: "text-coral-ink" },
};

export function WatchCard({ resident }: { resident: Resident }) {
  const [watch, setWatch] = useState<Watch | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const revision = useRef(0);
  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      const current = revision.current;
      try {
        const next = await api.watch();
        if (!disposed && current === revision.current) { setWatch(next); setError(""); }
      } catch {
        if (!disposed) setError("Couldn't reach watch mode. Please try again.");
      }
    };
    void poll();
    const timer = setInterval(poll, 5000);
    return () => { disposed = true; clearInterval(timer); };
  }, []);

  const change = async (minutes?: WatchRequest["minutes"]) => {
    revision.current += 1;
    setBusy(true);
    setError("");
    try {
      setWatch(minutes ? await api.startWatch({ minutes }) : await api.stopWatch());
    } catch {
      setError("Couldn't change watch mode. Please try again.");
    } finally {
      revision.current += 1;
      setBusy(false);
    }
  };

  const active = watch?.active;
  return (
    <section aria-labelledby="watch-heading" className="rounded-[var(--radius-card)] border border-line bg-surface p-6">
      <h2 id="watch-heading" className="font-serif text-[1.625rem] leading-tight font-medium">
        Keep an eye on {resident.family_name_for_her}
      </h2>
      <p className="mt-3 text-muted">The kitchen screen checks on her every few seconds and tells her so. Nothing is recorded.</p>
      {active && watch.until ? (
        <div className="mt-4 flex flex-wrap items-center gap-4">
          <p role="status">On until {formatTime(watch.until, resident.timezone)}</p>
          <Button variant="quiet" onClick={() => void change()} disabled={busy}>Turn off</Button>
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap gap-3">
          <Button onClick={() => void change(30)} disabled={busy || !watch}>30 minutes</Button>
          <Button variant="quiet" onClick={() => void change(120)} disabled={busy || !watch}>2 hours</Button>
        </div>
      )}
      {error ? <p role="alert" className="mt-3 text-coral-ink">{error}</p> : null}
      <div role="log" aria-label="Watch notes" aria-live="polite" aria-relevant="additions" className="mt-4">
        {active && watch.notes.length ? (
          <ul className="space-y-3">
            {[...watch.notes].reverse().map((note) => {
              const { Icon, label, color } = statuses[note.status];
              return <li key={`${note.ts}-${note.note}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <time dateTime={note.ts} className="text-sm text-muted">{formatTime(note.ts, resident.timezone)}</time>
                <span className={`inline-flex items-center gap-1 font-medium ${color}`}><Icon aria-hidden weight="fill" className="size-5 self-center" />{label}</span>
                <span>{note.note}</span>
              </li>;
            })}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
