import type {
  Alert,
  Checkin,
  CheckinKind,
  EventKind,
  EventSource,
  HearthEvent,
  ScheduleItem,
  Today,
} from "./contracts";

/** "2:41 pm" in her timezone. */
export function formatTime(iso: string | Date, timeZone?: string): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone,
  }).formatToParts(d);
  const h = parts.find((p) => p.type === "hour")?.value ?? "";
  const m = parts.find((p) => p.type === "minute")?.value ?? "";
  const ap = (parts.find((p) => p.type === "dayPeriod")?.value ?? "").toLowerCase();
  return `${h}:${m} ${ap}`.trim();
}

/** Split "2:41 pm" into numeral + period so the numeral can be set large. */
export function timeParts(iso: string, timeZone?: string): { clock: string; period: string } {
  const [clock, period = ""] = formatTime(iso, timeZone).split(" ");
  return { clock, period };
}

/** "14:30" → "2:30 pm" */
export function formatHHMM(hhmm: string): string {
  const [hs, ms] = hhmm.split(":");
  const h = Number(hs);
  const period = h >= 12 ? "pm" : "am";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${ms} ${period}`;
}

export function formatDay(iso: string, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", timeZone }).format(
    new Date(iso),
  );
}

export function formatLitres(ml: number): string {
  return `${Math.round(ml)} ml`;
}

export const KIND_NOUN: Record<CheckinKind, string> = { drink: "water", meal: "a meal", med: "tablets" };
export const KIND_LABEL: Record<CheckinKind, string> = { drink: "Water", meal: "Meal", med: "Tablets" };
export const REPORTED_DETAIL = "The helper heard her say so but couldn't see her on this call.";

/** Reports are notes with a check-in kind and a spoken gist, never observed actions. */
export function isReportedEvent(ev: HearthEvent): boolean {
  return ev.kind === "note" && ev.for_kind !== null && ev.confidence < 0.5 && ev.evidence.startsWith("Said:");
}

export function eventLabel(ev: HearthEvent): string {
  return isReportedEvent(ev) ? "Said so" : EVENT_LABEL[ev.kind];
}

export const EVENT_LABEL: Record<EventKind, string> = {
  drink: "Drink",
  meal: "Meal",
  med: "Tablets",
  refused: "Didn't want it",
  missed: "Missed",
  concern: "Concern",
  note: "Note",
};

export const SOURCE_LABEL: Record<EventSource, string> = {
  facetime: "FaceTime",
  inbound: "She called",
  kiosk: "Kitchen screen",
  manual: "You",
  mock: "Practice",
  seed: "FaceTime",
};

export function confidenceWords(c: number): string {
  if (c >= 0.8) return "Seen clearly";
  if (c >= 0.5) return "Probably";
  return "Not sure";
}

/** The status headline: answers "has she drunk?" in words. */
export function hydrationHeadline(today: Today, mum: string): string {
  const ml = Math.round(today.fluid_ml);
  if (ml <= 0) return `${mum} hasn't had a drink the helper has seen yet today.`;
  return `${mum} has had ${ml} ml today.`;
}

export function hydrationSub(today: Today): string {
  const left = Math.max(0, today.fluid_target_ml - today.fluid_ml);
  if (left <= 0) return `That's her ${today.fluid_target_ml} ml goal reached.`;
  return `${Math.round(left)} ml to go to her ${today.fluid_target_ml} ml goal.`;
}

export interface MedSlot {
  label: string;
  time: string;
  done: boolean;
}

/**
 * Today's slots for meals or tablets with done flags. The contract has no per-slot status, so this
 * matches today's confirmed events to scheduled items in time order.
 * TODO(contract): a `slots` list on Today would make this exact.
 */
export function slotsFor(today: Today, schedule: ScheduleItem[] | null, kind: "meal" | "med"): MedSlot[] {
  const items = (schedule ?? []).filter((s) => s.kind === kind && s.enabled).sort((a, b) => a.time.localeCompare(b.time));
  const done = kind === "med" ? today.meds_confirmed : today.meals_confirmed;
  if (items.length === 0) {
    const n = kind === "med" ? today.meds_expected : 0;
    return Array.from({ length: n }, (_, i) => ({
      label: i === 0 ? "Morning" : i === 1 ? "Evening" : `Dose ${i + 1}`,
      time: "",
      done: i < done,
    }));
  }
  return items.map((s, i) => ({ label: shortLabel(s), time: s.time, done: i < done }));
}

function shortLabel(s: ScheduleItem): string {
  const l = s.label.toLowerCase();
  if (s.kind === "med") {
    if (l.includes("morning")) return "Morning";
    if (l.includes("evening") || l.includes("night")) return "Evening";
    if (l.includes("lunch") || l.includes("midday")) return "Midday";
  }
  return s.label;
}

export function nextLine(today: Today): string | null {
  if (!today.next_checkin) return null;
  const when = today.next_checkin_at ? formatTime(today.next_checkin_at, today.resident.timezone) : formatHHMM(today.next_checkin.time);
  return `Next: ${KIND_NOUN[today.next_checkin.kind]} at ${when}`;
}

export function isActive(c: Checkin | null | undefined): boolean {
  return !!c && c.status !== "ended";
}

export function resultTitle(c: Checkin, ev: HearthEvent | undefined, mum: string): string {
  switch (c.outcome) {
    case "reported":
      return `${mum} says ${c.kind === "drink" ? "she's had her water" : c.kind === "meal" ? "she has eaten" : "she took her tablets"}`;
    case "confirmed":
      return ev?.summary ?? `${mum} ${c.kind === "drink" ? "had a drink" : c.kind === "meal" ? "ate" : "took her tablets"}`;
    case "refused":
      return `${mum} didn't want ${KIND_NOUN[c.kind]} just now`;
    case "no_answer":
      return `${mum} didn't answer`;
    case "concern":
      return "The check-in noticed something unusual";
    case "error":
      return "The check-in couldn't finish";
    default:
      return c.summary ?? "The check-in ended";
  }
}

export function alertSentence(a: Alert, tz?: string): string {
  if (a.kind === "concern" && !a.checkin_id) {
    // Watch mode: the kitchen screen's own check, not a call.
    const seen = `The kitchen screen saw: ${a.detail.replace(/\.$/, "")} (${formatTime(a.ts, tz)}).`;
    return a.tier === "urgent"
      ? `${seen} Please check on her now.`
      : `${seen} Seen once, so no alarm yet. Two in a row sends an urgent alert.`;
  }
  if (a.kind === "concern") {
    const said = a.said ? `she said ‘${a.said}’ at ${formatTime(a.ts, tz)}` : `at ${formatTime(a.ts, tz)}`;
    return `The check-in noticed something unusual: ${said}. Please check on her now.`;
  }
  return a.detail;
}
