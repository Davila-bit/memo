"use client";

import {
  ArrowClockwise,
  Broadcast,
  ClockCounterClockwise,
  FloppyDisk,
  Prohibit,
  Signature,
  Trash,
  WifiSlash,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import type { Privacy } from "@/lib/contracts";
import { useHearth } from "@/lib/store";
import { PageHeader } from "../page-header";
import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { Skeleton } from "../ui/skeleton";
import { StatusBadge } from "../ui/status-badge";
import { DeleteDialog } from "./delete-dialog";
import { countLabel, formatDate, RELATION } from "./privacy-words";

const TITLE = "What Memo keeps, and what it never keeps.";

export function PrivacyView() {
  const mum = useHearth((s) => s.me?.resident.family_name_for_her) ?? "Mum";
  const [privacy, setPrivacy] = useState<Privacy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const deleteButton = useRef<HTMLButtonElement>(null);
  // The sheet is opened without a Radix Trigger, so give focus back to the button ourselves
  // (after Radix has finished its own close handling).
  const setConfirm = (o: boolean) => {
    setConfirming(o);
    if (!o) setTimeout(() => deleteButton.current?.focus(), 0);
  };

  useEffect(() => {
    let cancelled = false;
    api
      .privacy()
      .then((p) => {
        if (cancelled) return;
        setPrivacy(p);
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        console.warn("[privacy] load failed", e);
        setError(e instanceof ApiError ? e.message : "Couldn't reach Memo.");
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const reload = () => setAttempt((n) => n + 1);
  const retry = () => {
    setError(null);
    setPrivacy(null);
    reload();
  };

  if (!privacy) return error ? <PrivacyError message={error} onRetry={retry} /> : <PrivacySkeleton />;

  // A stored thing the API marks as not kept belongs with the never-kept list, with its detail.
  const kept = privacy.stored.filter((s) => s.kept);
  const never = [
    ...privacy.never_stored.map((what) => ({ what, detail: null as string | null })),
    ...privacy.stored.filter((s) => !s.kept).map((s) => ({ what: s.what, detail: s.detail })),
  ];
  const counts = Object.entries(privacy.counts);
  const c = privacy.consent;

  return (
    <div className="space-y-6">
      <PageHeader title={TITLE} intro="Everything on this page comes straight from Memo's own records." />

      {counts.length > 0 ? (
        <section aria-labelledby="counts-heading" className="rounded-2xl border border-line bg-surface px-5 py-4">
          <h2 id="counts-heading" className="text-[0.8125rem] font-semibold tracking-[0.06em] text-muted uppercase">
            Stored right now
          </h2>
          <ul data-testid="privacy-counts" className="mt-1.5 flex flex-wrap gap-x-2 text-[1.0625rem]">
            {counts.map(([key, n], i) => (
              <li key={key} className="whitespace-nowrap">
                <span className="font-semibold tabular-nums">{n}</span> {countLabel(key, n)}
                {i < counts.length - 1 ? (
                  <span aria-hidden className="pl-2 text-muted">
                    ·
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 sm:gap-6">
        <Card aria-labelledby="kept-heading">
          <h2 id="kept-heading" className="font-serif text-[1.5rem] leading-tight font-medium">
            Kept
          </h2>
          {kept.length === 0 ? (
            <p className="mt-3 text-muted">Nothing is kept.</p>
          ) : (
            <ul data-testid="kept-list" className="mt-4 space-y-4">
              {kept.map((s) => (
                <Row key={s.what} badge={<StatusBadge status="info" size="sm" icon={FloppyDisk}>Kept</StatusBadge>} what={s.what} detail={s.detail} />
              ))}
            </ul>
          )}
        </Card>
        <Card tone="ok" aria-labelledby="never-heading">
          <h2 id="never-heading" className="font-serif text-[1.5rem] leading-tight font-medium">
            Never kept
          </h2>
          {never.length === 0 ? (
            <p className="mt-3 text-muted">Nothing listed.</p>
          ) : (
            <ul data-testid="never-list" className="mt-4 space-y-4">
              {never.map((s) => (
                <Row key={s.what} badge={<StatusBadge status="ok" size="sm" icon={Prohibit}>Never kept</StatusBadge>} what={s.what} detail={s.detail} />
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 sm:gap-6">
        <Card aria-labelledby="transit-heading">
          <Heading id="transit-heading" icon={<Broadcast aria-hidden className="size-6 text-sky-ink" weight="duotone" />}>
            Who sees a call while it&apos;s happening
          </Heading>
          <p className="mt-2 text-muted">Live video and sound pass through these services during the call and are not stored by Memo.</p>
          {privacy.in_transit.length > 0 ? (
            <ul className="mt-4 flex flex-wrap gap-2">
              {privacy.in_transit.map((t) => (
                <li key={t} className="rounded-full border border-line-strong/50 bg-paper px-3 py-1 text-[0.9375rem]">
                  {t}
                </li>
              ))}
            </ul>
          ) : null}
        </Card>
        <Card aria-labelledby="retention-heading">
          <Heading id="retention-heading" icon={<ClockCounterClockwise aria-hidden className="size-6 text-sky-ink" weight="duotone" />}>
            How long
          </Heading>
          <p className="mt-2 text-[1.0625rem]">Check-in notes are deleted after {privacy.retention_days_events} days.</p>
        </Card>
      </div>

      <Card aria-labelledby="consent-heading">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Heading id="consent-heading" icon={<Signature aria-hidden className="size-6 text-sky-ink" weight="duotone" />}>
            Consent
          </Heading>
          {c.status === "signed" ? (
            <StatusBadge status="ok">Signed</StatusBadge>
          ) : (
            <StatusBadge status="check">Pending</StatusBadge>
          )}
        </div>
        <p className="mt-2 text-[1.0625rem]" data-testid="consent-line">
          {c.status === "signed" ? (
            <>
              Agreed by <strong className="font-semibold">{c.signed_by}</strong> {RELATION[c.relation]}
              {c.signed_at ? <> on {formatDate(c.signed_at)}</> : null}.
            </>
          ) : (
            <>
              Waiting for <strong className="font-semibold">{c.signed_by || "her or her healthcare agent"}</strong>
              {c.signed_by ? ` (${RELATION[c.relation]})` : ""} to agree.
            </>
          )}
        </p>
      </Card>

      <Card tone="urgent" aria-labelledby="delete-heading">
        <Heading id="delete-heading" icon={<Trash aria-hidden className="size-6 text-coral-ink" weight="duotone" />}>
          Delete everything
        </Heading>
        <p className="mt-2 max-w-prose">
          Remove every check-in note, check-in record and alert about {mum}. You&apos;ll be asked to confirm first.
        </p>
        <Button ref={deleteButton} className="mt-4" variant="coral" onClick={() => setConfirm(true)}>
          <Trash aria-hidden className="size-5" weight="bold" />
          Delete everything…
        </Button>
      </Card>

      <DeleteDialog open={confirming} onOpenChange={setConfirm}notes={privacy.counts.events} mum={mum} onDeleted={reload} />
    </div>
  );
}

function Heading({ id, icon, children }: { id: string; icon: ReactNode; children: ReactNode }) {
  return (
    <h2 id={id} className="flex items-start gap-2 font-serif text-[1.375rem] leading-tight font-medium">
      <span className="flex h-[1.25em] shrink-0 items-center">{icon}</span>
      <span>{children}</span>
    </h2>
  );
}

function Row({ badge, what, detail }: { badge: ReactNode; what: string; detail: string | null }) {
  return (
    <li>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold text-ink">{what}</span>
        {badge}
      </div>
      {detail ? <p className="mt-0.5 text-[0.9375rem] text-muted">{detail}</p> : null}
    </li>
  );
}

function PrivacySkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading privacy" className="space-y-6">
      <div className="space-y-3">
        <Skeleton className="h-10 w-4/5" />
        <Skeleton className="h-5 w-2/5" />
      </div>
      <Skeleton className="h-16 w-full" />
      <div className="grid gap-4 sm:grid-cols-2">
        <Skeleton className="h-56" />
        <Skeleton className="h-56" />
      </div>
      <Skeleton className="h-28 w-full" />
    </div>
  );
}

function PrivacyError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <>
      <PageHeader title="Privacy" />
      <div role="alert" className="flex flex-col items-center rounded-[var(--radius-card)] border border-line bg-surface px-6 py-14 text-center">
        <WifiSlash aria-hidden className="size-10 text-amber-ink" weight="duotone" />
        <p className="mt-4 font-serif text-2xl font-medium">Couldn&apos;t load the privacy details.</p>
        <p className="mt-2 max-w-sm text-muted">{message}</p>
        <Button className="mt-6" variant="quiet" onClick={onRetry}>
          <ArrowClockwise aria-hidden className="size-5" /> Try again
        </Button>
      </div>
    </>
  );
}
