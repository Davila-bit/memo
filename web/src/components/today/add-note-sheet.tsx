"use client";

import { useId, useState } from "react";
import type { Amount, ManualEventRequest } from "@/lib/contracts";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { refreshToday } from "@/lib/live";
import { useHearth } from "@/lib/store";
import { Button } from "../ui/button";
import { Sheet } from "../ui/sheet";

const KINDS: { value: ManualEventRequest["kind"]; label: string }[] = [
  { value: "note", label: "Note" },
  { value: "drink", label: "Drink" },
  { value: "meal", label: "Meal" },
  { value: "med", label: "Tablets" },
];
const AMOUNTS: { value: Amount; label: string }[] = [
  { value: "sip", label: "A few sips" },
  { value: "some", label: "Some" },
  { value: "most", label: "Most of a glass" },
  { value: "all", label: "A full glass" },
];

export function AddNoteSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [kind, setKind] = useState<ManualEventRequest["kind"]>("note");
  const [amount, setAmount] = useState<Amount>("most");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const addEvent = useHearth((s) => s.addEvent);
  const toast = useHearth((s) => s.toast);
  const mum = useHearth((s) => s.today?.resident.family_name_for_her ?? "Mum");
  const textId = useId();
  const errId = useId();
  const hintId = useId();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const summary = text.trim();
    if (!summary) {
      setError("Please write a few words first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const ev = await api.addEvent({ kind, summary, amount: kind === "drink" ? amount : null });
      addEvent(ev);
      void refreshToday();
      toast("Added to today.", "ok");
      setText("");
      setKind("note");
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Add a note" description={`Something you saw or heard yourself. It goes on ${mum}'s timeline as from you.`}>
      <form onSubmit={submit} noValidate>
        <fieldset>
          <legend className="text-[0.9375rem] font-semibold">What is it?</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {KINDS.map((k) => (
              <label
                key={k.value}
                className={cn(
                  "ring-on-focus inline-flex min-h-11 cursor-pointer items-center rounded-full border px-4 text-sm font-medium",
                  kind === k.value ? "border-ink bg-ink text-surface" : "border-line-strong/60 bg-surface",
                )}
              >
                <input type="radio" name="note-kind" className="sr-only" checked={kind === k.value} onChange={() => setKind(k.value)} />
                {k.label}
              </label>
            ))}
          </div>
        </fieldset>
        {kind === "drink" ? (
          <fieldset className="mt-4">
            <legend className="text-[0.9375rem] font-semibold">How much?</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {AMOUNTS.map((a) => (
                <label
                  key={a.value}
                  className={cn(
                    "ring-on-focus inline-flex min-h-11 cursor-pointer items-center rounded-full border px-4 text-sm font-medium",
                    amount === a.value ? "border-ink bg-ink text-surface" : "border-line-strong/60 bg-surface",
                  )}
                >
                  <input type="radio" name="note-amount" className="sr-only" checked={amount === a.value} onChange={() => setAmount(a.value)} />
                  {a.label}
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}
        <label htmlFor={textId} className="mt-5 block text-[0.9375rem] font-semibold">
          Your note
        </label>
        <p id={hintId} className="mt-0.5 text-sm text-muted">
          For example: “Rang her at 11. She sounded bright.”
        </p>
        <textarea
          id={textId}
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={cn(hintId, error && errId)}
          className={cn("mt-2 block w-full rounded-2xl border-2 bg-surface px-4 py-3 text-[1.0625rem]", error ? "border-coral-ink" : "border-line-strong/70")}
        />
        {error ? (
          <p id={errId} role="alert" className="mt-2 text-[0.9375rem] font-medium text-coral-ink">
            {error}
          </p>
        ) : null}
        <Button type="submit" size="lg" className="mt-5 w-full" disabled={busy}>
          {busy ? "Saving…" : "Add to today"}
        </Button>
      </form>
    </Sheet>
  );
}
