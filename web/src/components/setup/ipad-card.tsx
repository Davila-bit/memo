"use client";

import { CheckCircle, DeviceTablet } from "@phosphor-icons/react";
import { useEffect, useId, useState } from "react";
import { Card } from "../ui/card";
import { StatusBadge } from "../ui/status-badge";

const KEY = "hearth.ipadChecklist";

const ITEMS: { title: string; detail: string }[] = [
  {
    title: "Turn on Auto-Answer.",
    detail: "Settings → Accessibility → Touch → Call Audio Routing → Auto-Answer Calls, set to 3 seconds.",
  },
  {
    title: "Save the helper as a contact.",
    detail: "Add the helper's number to Contacts as “Sarah's helper”, so she sees a familiar name.",
  },
  {
    title: "Let only family through.",
    detail: "Settings → Focus → Do Not Disturb → People → allow calls from Contacts only.",
  },
  {
    title: "Keep it awake and loud.",
    detail: "On the charger, on a stand facing her chair, volume up, Auto-Lock set to Never.",
  },
  {
    title: "Put her things in view.",
    detail: "Her own mug and pill box on the table where the camera can see them.",
  },
];

const EMPTY = ITEMS.map(() => false);

function load(): boolean[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return EMPTY;
    return ITEMS.map((_, i) => parsed[i] === true);
  } catch {
    return EMPTY;
  }
}

function save(ticks: boolean[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(ticks));
  } catch {
    // Private mode or storage blocked: the ticks just won't survive a reload.
  }
}

/** One-time checklist for the iPad in her kitchen. Remembered on this device only. */
export function IpadCard() {
  const [ticks, setTicks] = useState<boolean[]>(EMPTY);
  const id = useId();

  // Read after mount, never during render, so server and client HTML match.
  useEffect(() => {
    const stored = load();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reads localStorage, client only, after hydration
    if (stored.some(Boolean)) setTicks(stored);
  }, []);

  const toggle = (i: number, on: boolean) => {
    setTicks((old) => {
      const next = old.map((t, j) => (j === i ? on : t));
      save(next);
      return next;
    });
  };

  const count = ticks.filter(Boolean).length;

  return (
    <Card id="setup-ipad" className="scroll-mt-28" aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`} className="flex items-center gap-2 font-serif text-2xl">
        <DeviceTablet aria-hidden className="size-7 shrink-0 text-sage-ink" weight="duotone" />
        The kitchen iPad
      </h2>
      <p className="mt-2 text-muted">Do these once on the iPad in her kitchen, so she never has to touch the screen.</p>
      <ul className="mt-4 divide-y divide-line border-y border-line">
        {ITEMS.map((item, i) => {
          const box = `${id}-item-${i}`;
          return (
            <li key={item.title}>
              <label htmlFor={box} className="flex min-h-11 cursor-pointer items-start gap-3 py-3">
                <input
                  id={box}
                  type="checkbox"
                  checked={ticks[i]}
                  onChange={(e) => toggle(i, e.target.checked)}
                  className="mt-0.5 size-5 shrink-0 cursor-pointer accent-[var(--sage-ink)]"
                />
                <span className="min-w-0 flex-1">
                  <strong className="font-semibold text-ink">{item.title}</strong>{" "}
                  <span className="text-muted">{item.detail}</span>
                </span>
                {ticks[i] ? <CheckCircle aria-hidden className="mt-0.5 size-5 shrink-0 text-sage-ink" weight="fill" /> : null}
              </label>
            </li>
          );
        })}
      </ul>
      <p role="status" className="mt-4 flex flex-wrap items-center gap-3 text-[0.9375rem] text-muted">
        <span>
          {count} of {ITEMS.length} done
        </span>
        {count === ITEMS.length ? (
          <StatusBadge status="ok" size="sm">
            Ready
          </StatusBadge>
        ) : null}
      </p>
    </Card>
  );
}
