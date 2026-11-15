"use client";

import { Drop, ForkKnife, Pill, PhoneCall, type Icon } from "@phosphor-icons/react";
import { useEffect, useId, useState } from "react";
import type { CheckinKind, CheckinOutcome, Transport } from "@/lib/contracts";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { demoEnabled, IS_MOCK } from "@/lib/env";
import { useHearth } from "@/lib/store";
import { Button } from "../ui/button";
import { Sheet } from "../ui/sheet";

const KINDS: { value: CheckinKind; label: string; hint: string; icon: Icon }[] = [
  { value: "drink", label: "Water", hint: "Shows her blue mug", icon: Drop },
  { value: "meal", label: "Meal", hint: "Shows her plate", icon: ForkKnife },
  { value: "med", label: "Tablets", hint: "Shows the right pill-box slot", icon: Pill },
];

const TRANSPORTS: { value: Transport; label: string }[] = [
  { value: "facetime", label: "FaceTime" },
  { value: "kiosk", label: "Kitchen screen" },
  { value: "mock", label: "Practice" },
];

const ENDINGS: { value: CheckinOutcome; label: string }[] = [
  { value: "confirmed", label: "She has it" },
  { value: "refused", label: "She says no" },
  { value: "no_answer", label: "No answer" },
  { value: "concern", label: "Something's wrong" },
];

export function CheckinSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [kind, setKind] = useState<CheckinKind>("drink");
  const [transport, setTransport] = useState<Transport>(IS_MOCK ? "mock" : "facetime");
  const [ending, setEnding] = useState<CheckinOutcome>("confirmed");
  const [demo, setDemo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mum = useHearth((s) => s.today?.resident.family_name_for_her ?? "Mum");
  const setCheckin = useHearth((s) => s.setCheckin);
  const toast = useHearth((s) => s.toast);
  const errId = useId();

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reads ?demo=1 on the client
    setDemo(demoEnabled());
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- clear stale errors when reopened
    if (open) setError(null);
  }, [open]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const c = await api.startCheckin({
        kind,
        transport: demo ? transport : null,
        mock_outcome: demo && transport === "mock" ? ending : null,
      });
      setCheckin(c);
      onOpenChange(false);
      toast(`Calling ${mum} now.`, "info");
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "A check-in is already running. Please wait for it to finish."
          : e instanceof Error
            ? e.message
            : "Couldn't start the check-in. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={`Check on ${mum} now`}
      description={`The helper will call the kitchen iPad, show ${mum} what it's for, and tell you what it sees. It takes about a minute.`}
    >
      <fieldset>
        <legend className="text-[0.9375rem] font-semibold">What for?</legend>
        <div className="mt-3 grid gap-2.5">
          {KINDS.map((k) => (
            <label
              key={k.value}
              className={cn(
                "flex min-h-16 cursor-pointer items-center gap-4 rounded-2xl border-2 px-4 py-3 transition-colors ring-on-focus",
                kind === k.value ? "border-ink bg-paper" : "border-line hover:border-line-strong/60",
              )}
            >
              <input type="radio" name="kind" value={k.value} checked={kind === k.value} onChange={() => setKind(k.value)} className="sr-only" />
              <k.icon aria-hidden className={cn("size-7 shrink-0", kind === k.value ? "text-sky-ink" : "text-muted")} weight="duotone" />
              <span className="flex-1">
                <span className="block font-semibold">{k.label}</span>
                <span className="block text-sm text-muted">{k.hint}</span>
              </span>
              <span
                aria-hidden
                className={cn("flex size-6 items-center justify-center rounded-full border-2", kind === k.value ? "border-ink" : "border-line-strong")}
              >
                {kind === k.value ? <span className="size-3 rounded-full bg-ink" /> : null}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      {demo ? (
        <div className="mt-6 rounded-2xl border border-dashed border-line-strong/60 p-4">
          <p className="text-sm font-semibold text-muted">Demo options</p>
          <Segmented legend="Call through" name="transport" options={TRANSPORTS} value={transport} onChange={setTransport} />
          {transport === "mock" ? <Segmented legend="How it ends" name="ending" options={ENDINGS} value={ending} onChange={setEnding} /> : null}
        </div>
      ) : null}

      {error ? (
        <p id={errId} role="alert" className="mt-4 text-[0.9375rem] font-medium text-coral-ink">
          {error}
        </p>
      ) : null}

      <Button size="lg" className="mt-6 w-full" onClick={start} disabled={busy} aria-describedby={error ? errId : undefined}>
        <PhoneCall aria-hidden className="size-5" weight="fill" />
        {busy ? "Starting…" : `Call ${mum} now`}
      </Button>
      <p className="mt-3 text-center text-sm text-muted">Live only. Nothing is recorded.</p>
    </Sheet>
  );
}

function Segmented<T extends string>({
  legend,
  name,
  options,
  value,
  onChange,
}: {
  legend: string;
  name: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <fieldset className="mt-3">
      <legend className="text-sm text-muted">{legend}</legend>
      <div className="mt-1.5 flex flex-wrap gap-2">
        {options.map((o) => (
          <label
            key={o.value}
            className={cn(
              "inline-flex min-h-11 cursor-pointer items-center rounded-full border px-4 text-sm font-medium ring-on-focus",
              value === o.value ? "border-ink bg-ink text-surface" : "border-line-strong/60 bg-surface text-ink hover:border-line-strong",
            )}
          >
            <input type="radio" name={name} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} className="sr-only" />
            {o.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
