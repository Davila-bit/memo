"use client";

import { create } from "zustand";
import type { Alert, Checkin, HearthEvent, Me, ScheduleItem, Today, WsMessage } from "./contracts";

export interface Caption {
  id: number;
  checkin_id: string;
  speaker: "helper" | "her";
  text: string;
}

export type Connection = "connecting" | "live" | "polling";

export interface Toast {
  id: number;
  text: string;
  tone: "info" | "ok" | "warn";
}

interface HearthState {
  me: Me | null;
  today: Today | null;
  schedule: ScheduleItem[] | null;
  /** Alerts Sarah already acted on; shown folded into the timeline. */
  handledAlerts: Alert[];
  loadError: string | null;
  connection: Connection;
  /** The active check-in, or the one that just finished (until dismissed). */
  checkin: Checkin | null;
  resultDismissed: boolean;
  captions: Caption[];
  /** Polite announcement for screen readers (new timeline items, status changes). */
  announcement: string;
  toasts: Toast[];

  setMe(me: Me | null): void;
  setToday(t: Today): void;
  setSchedule(s: ScheduleItem[]): void;
  setHandledAlerts(a: Alert[]): void;
  setLoadError(e: string | null): void;
  setConnection(c: Connection): void;
  setCheckin(c: Checkin | null): void;
  dismissResult(): void;
  applyMessage(m: WsMessage): void;
  upsertAlert(a: Alert): void;
  addEvent(e: HearthEvent): void;
  announce(text: string): void;
  toast(text: string, tone?: Toast["tone"]): void;
  dropToast(id: number): void;
  reset(): void;
}

let n = 0;

function upsertById<T extends { id: string }>(list: T[], item: T): T[] {
  const i = list.findIndex((x) => x.id === item.id);
  if (i === -1) return [item, ...list];
  const copy = list.slice();
  copy[i] = item;
  return copy;
}

export const useHearth = create<HearthState>((set, get) => ({
  me: null,
  today: null,
  schedule: null,
  handledAlerts: [],
  loadError: null,
  connection: "connecting",
  checkin: null,
  resultDismissed: false,
  captions: [],
  announcement: "",
  toasts: [],

  setMe: (me) => set({ me }),
  setToday: (t) => {
    const cur = get().checkin;
    const patch: Partial<HearthState> = { today: t, loadError: null };
    if (t.active_checkin) {
      // Prefer whichever copy is further along (the socket can be ahead of a poll).
      if (!cur || cur.id !== t.active_checkin.id || rank(t.active_checkin) >= rank(cur)) {
        if (!cur || cur.id !== t.active_checkin.id) {
          patch.resultDismissed = false;
        }
        patch.checkin = t.active_checkin;
      }
    }
    set(patch);
  },
  setSchedule: (schedule) => set({ schedule }),
  setHandledAlerts: (handledAlerts) => set({ handledAlerts }),
  setLoadError: (loadError) => set({ loadError }),
  setConnection: (connection) => set({ connection }),
  setCheckin: (c) => {
    const cur = get().checkin;
    if (c && cur && cur.id === c.id && rank(c) < rank(cur)) return;
    set({ checkin: c, resultDismissed: c && cur?.id === c.id ? get().resultDismissed : false });
  },
  dismissResult: () => set({ resultDismissed: true }),
  applyMessage: (m) => {
    const s = get();
    switch (m.type) {
      case "checkin":
        s.setCheckin(m.checkin);
        break;
      case "event":
        s.addEvent(m.event);
        break;
      case "alert":
        s.upsertAlert(m.alert);
        break;
      case "caption":
        set({
          captions: [...s.captions.filter((c) => c.checkin_id === m.checkin_id), { id: ++n, checkin_id: m.checkin_id, speaker: m.speaker, text: m.text }].slice(-6),
        });
        break;
      case "clock":
        if (s.today) set({ today: { ...s.today, now: m.now } });
        break;
    }
  },
  upsertAlert: (a) => {
    const t = get().today;
    if (!t) {
      if (a.status === "handled") set({ handledAlerts: upsertById(get().handledAlerts, a) });
      return;
    }
    const open = a.status === "open" ? upsertById(t.open_alerts, a) : t.open_alerts.filter((x) => x.id !== a.id);
    const handled = a.status === "handled" ? upsertById(get().handledAlerts, a) : get().handledAlerts.filter((x) => x.id !== a.id);
    set({ today: { ...t, open_alerts: open }, handledAlerts: handled });
  },
  addEvent: (e) => {
    const t = get().today;
    if (!t || t.events.some((x) => x.id === e.id)) return;
    set({ today: { ...t, events: [e, ...t.events].sort((a, b) => b.ts.localeCompare(a.ts)) } });
  },
  announce: (text) => set({ announcement: text }),
  toast: (text, tone = "info") => {
    const id = ++n;
    set({ toasts: [...get().toasts, { id, text, tone }] });
    setTimeout(() => get().dropToast(id), 6000);
  },
  dropToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  reset: () => set({ me: null, today: null, schedule: null, handledAlerts: [], checkin: null, captions: [], loadError: null, resultDismissed: false }),
}));

const ORDER = { queued: 0, ringing: 1, connected: 2, watching: 3, ended: 4 } as const;
function rank(c: Checkin): number {
  return ORDER[c.status] + (c.outcome ? 0.5 : 0);
}
