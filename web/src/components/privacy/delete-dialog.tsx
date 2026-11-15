"use client";

import { Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { api } from "@/lib/api";
import { refreshToday } from "@/lib/live";
import { useHearth } from "@/lib/store";
import { Button } from "../ui/button";
import { Sheet } from "../ui/sheet";

/**
 * Confirmation for "Delete everything". Says exactly what the API removes (events, check-ins,
 * alerts) and what it leaves. Radix focuses the Close button first, so the destructive button is
 * never the default; Escape cancels.
 */
export function DeleteDialog({
  open,
  onOpenChange,
  notes,
  mum,
  onDeleted,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  notes: number | undefined;
  mum: string;
  onDeleted: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useHearth((s) => s.toast);

  const change = (o: boolean) => {
    if (busy) return;
    if (!o) setError(null);
    onOpenChange(o);
  };

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const { deleted_events: n } = await api.deleteAllData();
      toast(n === 1 ? "Deleted 1 check-in note." : `Deleted ${n} check-in notes.`, "ok");
      void refreshToday();
      onDeleted();
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't delete just now. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={change} title="Delete everything?" description="This can't be undone.">
      <div className="space-y-3 text-[1.0625rem]">
        <p>This removes, for good:</p>
        <ul className="list-disc space-y-1 pl-6">
          <li>{notes === undefined ? "Every check-in note" : `All ${notes} check-in ${notes === 1 ? "note" : "notes"}`} about {mum}</li>
          <li>Every check-in record and alert</li>
        </ul>
        <p className="text-muted">
          Her schedule, the cue photos you added and the consent record stay, so check-ins carry on as set.
        </p>
      </div>
      {error ? (
        <p role="alert" className="mt-4 rounded-xl bg-coral-tint px-4 py-3 text-[0.9375rem] text-coral-ink">
          {error}
        </p>
      ) : null}
      <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Button variant="quiet" onClick={() => change(false)} disabled={busy}>
          Keep everything
        </Button>
        <Button variant="coral" onClick={confirm} disabled={busy}>
          <Trash aria-hidden className="size-5" weight="bold" />
          {busy ? "Deleting…" : "Delete everything"}
        </Button>
      </div>
    </Sheet>
  );
}
