"use client";

import { Check, Play } from "@phosphor-icons/react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { Consent, Resident } from "@/lib/contracts";
import { isActive } from "@/lib/format";
import { refreshToday } from "@/lib/live";
import { useHearth } from "@/lib/store";
import { PageHeader } from "../page-header";
import { Button, buttonClasses } from "../ui/button";
import { Card } from "../ui/card";
import { Skeleton } from "../ui/skeleton";
import { StatusBadge } from "../ui/status-badge";
import { IpadCard } from "./ipad-card";

const FIELD = "mt-2 block min-h-11 w-full rounded-xl border border-line-strong bg-surface px-3 py-2 text-base";
const STEPS = ["Names", "Daily target", "Consent", "Practice"];

export function SetupView() {
  const [resident, setResident] = useState<Resident | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    api.resident().then((r) => { if (!cancelled) { setResident(r); setError(null); } }).catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load setup."); });
    return () => { cancelled = true; };
  }, [attempt]);
  return <>
    <PageHeader title="A little setup, a familiar voice" intro="Help the helper make each check-in feel comfortable." />
    {error ? <Card tone="check"><p role="alert">{error}</p><Button variant="quiet" className="mt-3" onClick={() => setAttempt((n) => n + 1)}>Try again</Button></Card> : resident ? <SetupForm initial={resident} /> : <div aria-label="Loading setup" aria-busy="true" className="space-y-4"><Skeleton className="h-20" /><Skeleton className="h-80" /></div>}
  </>;
}

function SetupForm({ initial }: { initial: Resident }) {
  const [saved, setSaved] = useState(initial);
  const [names, setNames] = useState({ name: initial.name, family_name_for_her: initial.family_name_for_her, preferred_name: initial.preferred_name });
  const [target, setTarget] = useState(String(initial.fluid_target_ml));
  const [relation, setRelation] = useState<Consent["relation"]>(initial.consent.relation);
  const [signer, setSigner] = useState(initial.consent.signed_by);
  const [date, setDate] = useState(initial.consent.signed_at?.slice(0, 10) ?? "");
  const [done, setDone] = useState<number[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [practice, setPractice] = useState(false);
  const toast = useHearth((s) => s.toast);
  const checkin = useHearth((s) => s.checkin);
  const mum = saved.family_name_for_her;
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: saved.timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

  function dirty(step: number) { setDone((old) => old.filter((n) => n !== step)); }
  const props = (field: string) => ({ "aria-invalid": errors[field] ? true as const : undefined, "aria-describedby": errors[field] ? `setup-${field}-error` : undefined });
  const error = (field: string) => errors[field] ? <p id={`setup-${field}-error`} role="alert" className="mt-2 text-sm text-coral-ink">{errors[field]}</p> : null;

  async function save(step: number, patch: Partial<Resident>) {
    if (busy !== null) return;
    setBusy(step);
    setErrors({});
    try {
      const next = await api.updateResident({ ...saved, ...patch });
      setSaved(next);
      const state = useHearth.getState();
      state.setMe({ voice: state.me?.voice ?? false, watch: state.me?.watch ?? false, name: state.me?.name ?? next.daughter_name, resident: next });
      void refreshToday();
      setDone((old) => [...old.filter((n) => n !== step), step]);
      toast("Saved.", "ok");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Couldn't save this step. Please try again.";
      setErrors({ [`step-${step}`]: message });
      toast(message, "warn");
    } finally { setBusy(null); }
  }

  function saveNames(e: React.FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    for (const [key, value] of Object.entries(names)) if (!value.trim()) next[key] = "Please add a name here.";
    setErrors(next);
    if (!Object.keys(next).length) void save(1, { name: names.name.trim(), family_name_for_her: names.family_name_for_her.trim(), preferred_name: names.preferred_name.trim() });
  }

  function saveTarget(e: React.FormEvent) {
    e.preventDefault();
    const ml = Number(target);
    if (!target.trim() || !Number.isInteger(ml) || ml <= 0) { setErrors({ target: "Enter a daily target in whole millilitres, greater than zero." }); return; }
    void save(2, { fluid_target_ml: ml });
  }

  function saveConsent(e: React.FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!signer.trim()) next.signer = "Add the name of the person who agreed.";
    const parsed = new Date(`${date}T12:00:00.000Z`);
    if (!date || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date || date > today) next.date = "Choose the date consent was given, today or earlier.";
    setErrors(next);
    if (!Object.keys(next).length) void save(3, { consent: { status: "signed", signed_by: signer.trim(), relation, signed_at: parsed.toISOString() } });
  }

  async function runPractice() {
    if (busy !== null || isActive(checkin)) return;
    setBusy(4);
    setErrors({});
    try {
      const started = await api.startCheckin({ kind: "drink", transport: "mock" });
      useHearth.getState().setCheckin(started);
      setPractice(true);
      setDone((old) => [...old.filter((n) => n !== 4), 4]);
      toast("Practice check-in started.", "ok");
    } catch (err) { setErrors({ "step-4": err instanceof Error ? err.message : "Couldn't start the practice check-in. Please try again." }); }
    finally { setBusy(null); }
  }

  return <>
    <nav aria-label="Setup progress" className="mb-6 rounded-2xl border border-line bg-surface px-4 py-4">
      <p className="mb-3 text-sm text-muted" role="status">{done.length} of 4 steps complete</p>
      <ol className="grid grid-cols-4 gap-2">{STEPS.map((label, i) => <li key={label}><a href={`#setup-step-${i + 1}`} className="flex min-h-11 flex-col items-center gap-2 rounded-xl text-center text-xs sm:text-sm"><span className={`flex size-8 items-center justify-center rounded-full border font-semibold ${done.includes(i + 1) ? "border-sage/40 bg-sage-tint text-sage-ink" : "border-line-strong text-muted"}`}>{done.includes(i + 1) ? <Check aria-hidden className="size-4" /> : i + 1}</span><span>{label}<span className="sr-only">{done.includes(i + 1) ? ", complete" : ", not complete"}</span></span></a></li>)}</ol>
    </nav>
    <div className="space-y-5">
      <Step number={1} title={`Getting to know ${mum}`} done={done.includes(1)}>
        <form noValidate onSubmit={saveNames} aria-describedby={errors["step-1"] ? "setup-step-1-error" : undefined}>
          <fieldset disabled={busy !== null} className="space-y-4">
            <div><label htmlFor="setup-name" className="font-semibold">Her full name</label><input id="setup-name" autoComplete="off" className={FIELD} maxLength={100} value={names.name} onChange={(e) => { setNames({ ...names, name: e.target.value }); dirty(1); }} {...props("name")} />{error("name")}</div>
            <div className="grid gap-4 sm:grid-cols-2"><div><label htmlFor="setup-family-name" className="font-semibold">What you call her</label><input id="setup-family-name" className={FIELD} maxLength={60} value={names.family_name_for_her} onChange={(e) => { setNames({ ...names, family_name_for_her: e.target.value }); dirty(1); }} {...props("family_name_for_her")} />{error("family_name_for_her")}</div><div><label htmlFor="setup-preferred-name" className="font-semibold">What the helper calls her</label><input id="setup-preferred-name" className={FIELD} maxLength={60} value={names.preferred_name} onChange={(e) => { setNames({ ...names, preferred_name: e.target.value }); dirty(1); }} {...props("preferred_name")} />{error("preferred_name")}</div></div>
            {error("step-1")}<Button type="submit">{busy === 1 ? "Saving…" : "Save names"}</Button>
          </fieldset>
        </form>
      </Step>
      <Step number={2} title="A daily fluid target" done={done.includes(2)}>
        <p className="mb-4 text-muted">Use the target you already follow for {mum}.</p>
        <form noValidate onSubmit={saveTarget} aria-describedby={errors["step-2"] ? "setup-step-2-error" : undefined}><fieldset disabled={busy !== null}>
          <label htmlFor="setup-target" className="font-semibold">Daily fluid target (ml)</label><input id="setup-target" type="number" inputMode="numeric" min="1" step="1" className={`${FIELD} sm:max-w-64`} value={target} onChange={(e) => { setTarget(e.target.value); dirty(2); }} {...props("target")} />{error("target")}{error("step-2")}<Button type="submit" className="mt-4">{busy === 2 ? "Saving…" : "Save daily target"}</Button>
        </fieldset></form>
      </Step>
      <Step number={3} title="Record her consent" done={done.includes(3)}>
        <p className="mb-2 text-muted">Memo keeps your setup details, cue photos and short check-in notes; call video, audio and transcripts are processed live and aren&apos;t saved by Memo.</p>
        <Link href="/privacy" className="mb-4 inline-flex min-h-11 items-center rounded-lg text-sky-ink underline underline-offset-4">Read about privacy</Link>
        <form noValidate onSubmit={saveConsent} aria-describedby={errors["step-3"] ? "setup-step-3-error" : undefined}><fieldset disabled={busy !== null} className="space-y-4">
          <div><label htmlFor="setup-relation" className="font-semibold">Who agreed to the check-ins?</label><select id="setup-relation" className={FIELD} value={relation} onChange={(e) => { setRelation(e.target.value as Consent["relation"]); dirty(3); }}><option value="self">Herself</option><option value="healthcare_agent">Her healthcare agent</option><option value="guardian">Her guardian</option></select></div>
          <div className="grid gap-4 sm:grid-cols-2"><div><label htmlFor="setup-signer" className="font-semibold">Their full name</label><input id="setup-signer" className={FIELD} maxLength={100} value={signer} onChange={(e) => { setSigner(e.target.value); dirty(3); }} {...props("signer")} />{error("signer")}</div><div><label htmlFor="setup-consent-date" className="font-semibold">Date they agreed</label><input id="setup-consent-date" type="date" max={today} className={FIELD} value={date} onChange={(e) => { setDate(e.target.value); dirty(3); }} {...props("date")} />{error("date")}</div></div>
          {error("step-3")}<Button type="submit">{busy === 3 ? "Saving…" : "Save consent record"}</Button>
        </fieldset></form>
      </Step>
      <Step number={4} title="Try it together" done={done.includes(4)}>
        <p className="text-muted">A practice water check-in uses made-up observations. It won&apos;t place a real call.</p>
        <div className="mt-4 flex flex-wrap gap-3"><Button variant="sage" disabled={busy !== null || isActive(checkin)} onClick={() => void runPractice()}><Play aria-hidden className="size-5" />{busy === 4 ? "Starting…" : "Run a practice check-in"}</Button><Link href="/" className={buttonClasses("quiet")}>Go to Today</Link></div>
        {error("step-4")}<p role="status" className="mt-3 text-sm text-sage-ink">{practice ? "Practice started. Open Today to follow the check-in." : isActive(checkin) ? "A check-in is already running. You can follow it on Today." : ""}</p>
      </Step>
      <IpadCard />
    </div>
  </>;
}

function Step({ number, title, done, children }: { number: number; title: string; done: boolean; children: ReactNode }) {
  return <Card id={`setup-step-${number}`} className="scroll-mt-28" aria-labelledby={`setup-heading-${number}`}><div className="mb-5 flex flex-wrap items-center justify-between gap-3"><h2 id={`setup-heading-${number}`} className="font-serif text-2xl"><span className="mr-2 text-muted">{number}.</span>{title}</h2>{done ? <StatusBadge status="ok" size="sm">Complete</StatusBadge> : null}</div>{children}</Card>;
}
