// In-browser fake of the Memo API + live channel (NEXT_PUBLIC_HEARTH_MOCK=1).
// Believable seeded data (today + 3 earlier days) and a scripted ~9 s check-in whose ending
// is chosen with StartCheckinRequest.mock_outcome. Nothing leaves the browser.
import { ApiError, notifyUnauthorized } from "./api-error";
import type { HearthApi } from "./api";
import type {
  Alert,
  AlertAction,
  Checkin,
  CheckinKind,
  CheckinOutcome,
  Clock,
  Cue,
  DayStats,
  HearthEvent,
  Me,
  Privacy,
  Resident,
  ScheduleItem,
  Today,
  Watch,
  WsMessage,
} from "./contracts";
import { AMOUNT_ML } from "./contracts";
import { formatTime } from "./format";

type Listener = (m: WsMessage) => void;

const SESSION_KEY = "hearth.mock.session";
const PASSCODE = "hearth-demo";

const tz = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/New_York";
  } catch {
    return "America/New_York";
  }
})();

// Explicit browser-test opt-in, read only by mock mode. Ordinary practice stays off.
function watchEnabled(): boolean {
  return typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mockWatch") === "1";
}
let mockWatch: Watch = { enabled: false, active: false, until: null, notes: [] };

let seq = 0;
const id = (p: string) => `${p}_${Date.now().toString(36)}${(seq++).toString(36)}`;

interface State {
  resident: Resident;
  schedule: ScheduleItem[];
  cues: Cue[];
  events: HearthEvent[];
  alerts: Alert[];
  checkins: Checkin[];
  offsetMinutes: number;
  startNowMs: number;
  startRealMs: number;
}

let state: State | null = null;
const listeners = new Set<Listener>();
const timers: ReturnType<typeof setTimeout>[] = [];

function at(dayOffset: number, hhmm: string, base = new Date()): Date {
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date(base);
  d.setDate(d.getDate() + dayOffset);
  d.setHours(h, m, Math.floor(Math.random() * 50), 0);
  return d;
}

function now(): Date {
  const s = S();
  return new Date(s.startNowMs + (Date.now() - s.startRealMs) + s.offsetMinutes * 60_000);
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function emit(m: WsMessage): void {
  for (const l of listeners) l(m);
}

function ev(
  ts: Date,
  kind: HearthEvent["kind"],
  summary: string,
  evidence: string,
  extra: Partial<HearthEvent> = {},
): HearthEvent {
  return {
    id: id("ev"),
    ts: ts.toISOString(),
    resident_id: "res_margaret",
    checkin_id: null,
    for_kind: null,
    kind,
    source: "facetime",
    confidence: 0.88,
    summary,
    evidence,
    amount: null,
    volume_ml_est: null,
    med_steps: null,
    said: null,
    concern_category: null,
    ...extra,
  };
}

function seed(): State {
  const startNow = new Date();
  startNow.setHours(14, 35, 0, 0);
  const base = startNow;
  const resident: Resident = {
    id: "res_margaret",
    name: "Margaret Ellis",
    preferred_name: "Margaret",
    family_name_for_her: "Mum",
    daughter_name: "Sarah",
    phone: null,
    fluid_target_ml: 1500,
    timezone: tz,
    quiet_hours: ["21:00", "07:30"],
    consent: {
      status: "signed",
      signed_by: "Margaret Ellis",
      relation: "self",
      signed_at: at(-12, "11:00", base).toISOString(),
    },
  };
  const cues: Cue[] = [
    { id: "cue_mug", kind: "drink", title: "Her blue mug", line: "Margaret, let's have some water.", image_url: null, slot_label: null },
    { id: "cue_plate", kind: "meal", title: "Her lunch plate", line: "Shall we have a bite to eat?", image_url: null, slot_label: null },
    { id: "cue_pills", kind: "med", title: "Pill box", line: "Time for your tablets.", image_url: null, slot_label: ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"][new Date().getDay()] },
  ];
  const sch = (sid: string, kind: CheckinKind, time: string, label: string, cue: string): ScheduleItem => ({
    id: sid,
    resident_id: "res_margaret",
    kind,
    time,
    label,
    cue_id: cue,
    enabled: true,
  });
  const schedule: ScheduleItem[] = [
    sch("sch_med_am", "med", "08:00", "Morning tablets", "cue_pills"),
    sch("sch_meal_bfast", "meal", "08:30", "Breakfast", "cue_plate"),
    sch("sch_drink_1030", "drink", "10:30", "Mid-morning water", "cue_mug"),
    sch("sch_meal_lunch", "meal", "12:30", "Lunch", "cue_plate"),
    sch("sch_drink_1230", "drink", "12:30", "Water with lunch", "cue_mug"),
    sch("sch_drink_1530", "drink", "15:30", "Afternoon water", "cue_mug"),
    sch("sch_meal_supper", "meal", "18:00", "Supper", "cue_plate"),
    sch("sch_drink_1800", "drink", "18:00", "Water with supper", "cue_mug"),
    { ...sch("sch_med_pm", "med", "19:00", "Evening tablets", "cue_pills"), enabled: false }, // one tablet time a day
  ];

  const events: HearthEvent[] = [];
  const alerts: Alert[] = [];
  const allSteps: HearthEvent["med_steps"] = ["slot_opened", "pill_in_hand", "hand_to_mouth", "drank_water", "slot_shown_empty"];

  // Earlier days: full days, with the odd refusal and one missed check-in.
  for (let d = -3; d <= -1; d++) {
    events.push(
      ev(at(d, "08:04", base), "med", "Mum took her morning tablets", "Opened the morning slot, tablet in hand, sip of water, showed the slot empty", { med_steps: allSteps, confidence: 0.9 }),
      ev(at(d, "08:07", base), "drink", "Mum drank a full cup of tea", "Held up the blue mug, drank, showed it nearly empty", { amount: "all", volume_ml_est: 240, confidence: 0.91 }),
      ev(at(d, "08:41", base), "meal", "Mum ate most of her porridge", "Showed her bowl, ate several spoonfuls", { amount: "most", confidence: 0.82 }),
      ev(at(d, "10:33", base), "drink", "Mum drank most of a glass of water", "Showed the glass, drank twice, showed it again", { amount: "most", volume_ml_est: 180, confidence: 0.86 }),
      ev(at(d, "12:34", base), "meal", "Mum had some of her sandwich", "Showed her plate, ate a few bites", { amount: "some", confidence: 0.71 }),
    );
    if (d === -2) {
      events.push(ev(at(d, "15:31", base), "missed", "Mum didn't answer her 3:30 check-in", "No answer after two tries", { confidence: 1 }));
    } else {
      events.push(ev(at(d, "15:32", base), "drink", "Mum drank most of a glass of water", "Held up the blue mug, drank twice", { amount: "most", volume_ml_est: 180, confidence: 0.84 }));
    }
    if (d === -1) {
      events.push(
        ev(at(d, "18:02", base), "refused", "Mum didn't want water with supper", "Said she wasn't thirsty", { said: "I've had plenty, thank you", confidence: 0.9 }),
      );
    } else {
      events.push(ev(at(d, "18:03", base), "drink", "Mum drank a full glass with supper", "Showed the glass, drank, showed it empty", { amount: "all", volume_ml_est: 240, confidence: 0.9 }));
    }
    events.push(
      ev(at(d, "18:20", base), "meal", "Mum ate most of her supper", "Showed her plate, ate steadily", { amount: "most", confidence: 0.8 }),
    );
  }

  // Today, up to 2:35 pm: 640 ml so far.
  events.push(
    ev(at(0, "08:04", base), "med", "Mum took her morning tablets", "Opened the morning slot, tablet in hand, sip of water, showed the slot empty", { med_steps: allSteps, confidence: 0.9 }),
    ev(at(0, "08:06", base), "drink", "Mum drank a full cup of tea", "Held up the blue mug, drank, showed it nearly empty", { amount: "all", volume_ml_est: 240, confidence: 0.92 }),
    ev(at(0, "08:40", base), "meal", "Mum ate most of her porridge", "Showed her bowl, ate several spoonfuls", { amount: "most", confidence: 0.83 }),
    ev(at(0, "10:32", base), "drink", "Mum drank most of a glass of water", "Showed the glass, drank twice, showed it again", { amount: "most", volume_ml_est: 180, confidence: 0.87 }),
    ev(at(0, "11:15", base), "note", "Rang her. She sounded bright and talked about the garden.", "", { source: "manual", confidence: 1 }),
    ev(at(0, "12:31", base), "meal", "Mum had some of her sandwich", "Showed her plate, ate a few bites", { amount: "some", confidence: 0.68 }),
    ev(at(0, "12:33", base), "drink", "Mum drank most of a glass of water", "Held up the glass, drank, showed it again", { amount: "most", volume_ml_est: 180, confidence: 0.85 }),
  );
  const refused = ev(at(0, "13:30", base), "refused", "Mum didn't want water just then", "Said she'd had tea", {
    said: "Not now, love, I've just had tea",
    confidence: 0.9,
  });
  events.push(refused);
  alerts.push({
    id: "al_seed_refused",
    ts: refused.ts,
    resident_id: "res_margaret",
    checkin_id: null,
    event_id: refused.id,
    kind: "refused",
    tier: "check",
    title: "Mum didn't want her water",
    detail: "She said she'd just had tea.",
    said: refused.said,
    status: "handled",
    action: "retry_later",
    action_at: at(0, "13:32", base).toISOString(),
    retry_at: at(0, "14:02", base).toISOString(),
  });
  events.push(
    ev(at(0, "14:04", base), "drink", "Mum had a few sips of water", "Showed the blue mug, took two small sips", { amount: "sip", volume_ml_est: 40, confidence: 0.74 }),
  );

  events.sort((a, b) => b.ts.localeCompare(a.ts));
  return {
    resident,
    schedule,
    cues,
    events,
    alerts,
    checkins: [],
    offsetMinutes: 0,
    startNowMs: startNow.getTime(),
    startRealMs: Date.now(),
  };
}

function S(): State {
  if (!state) state = seed();
  return state;
}

function requireSession(): void {
  let ok = false;
  try {
    ok = sessionStorage.getItem(SESSION_KEY) === "1";
  } catch {
    ok = false;
  }
  if (!ok) {
    notifyUnauthorized();
    throw new ApiError(401, "Please sign in again.");
  }
}

const delay = <T>(v: T, ms = 120): Promise<T> => new Promise((r) => setTimeout(() => r(structuredClone(v)), ms));

function nextCheckin(n: Date): { item: ScheduleItem | null; at: string | null } {
  const s = S();
  const hhmm = `${String(n.getHours()).padStart(2, "0")}:${String(n.getMinutes()).padStart(2, "0")}`;
  const sorted = s.schedule.filter((x) => x.enabled).sort((a, b) => a.time.localeCompare(b.time) || (a.kind === "drink" ? -1 : 1));
  const later = sorted.find((x) => x.time > hhmm);
  if (later) {
    const d = new Date(n);
    const [h, m] = later.time.split(":").map(Number);
    d.setHours(h, m, 0, 0);
    return { item: later, at: d.toISOString() };
  }
  const first = sorted[0];
  if (!first) return { item: null, at: null };
  const d = new Date(n);
  d.setDate(d.getDate() + 1);
  const [h, m] = first.time.split(":").map(Number);
  d.setHours(h, m, 0, 0);
  return { item: first, at: d.toISOString() };
}

function buildToday(): Today {
  const s = S();
  const n = now();
  const events = s.events.filter((e) => sameDay(new Date(e.ts), n) && new Date(e.ts) <= n);
  const nx = nextCheckin(n);
  const active = s.checkins.find((c) => c.status !== "ended") ?? null;
  return {
    now: n.toISOString(),
    resident: s.resident,
    fluid_ml: events.filter((e) => e.kind === "drink").reduce((t, e) => t + (e.volume_ml_est ?? 0), 0),
    fluid_target_ml: s.resident.fluid_target_ml,
    drinks_confirmed: events.filter((e) => e.kind === "drink").length,
    meals_confirmed: events.filter((e) => e.kind === "meal").length,
    meds_confirmed: events.filter((e) => e.kind === "med").length,
    meds_expected: s.schedule.filter((x) => x.kind === "med" && x.enabled).length,
    next_checkin: nx.item,
    next_checkin_at: nx.at,
    active_checkin: active,
    open_alerts: s.alerts.filter((a) => a.status === "open").sort((a, b) => b.ts.localeCompare(a.ts)),
    events,
  };
}

// ---- scripted check-in

const LINES: Record<CheckinKind, { ask: string; show: string; then: string }> = {
  drink: { ask: "Could you show me your blue mug?", show: "Here it is.", then: "Lovely. Have a drink with me." },
  meal: { ask: "Could you show me your plate?", show: "It's a cheese sandwich.", then: "That looks nice. Have a bite or two." },
  med: { ask: "Could you open the morning slot of your pill box?", show: "This one?", then: "That's the one. Take your tablet with a sip of water." },
};

function updateCheckin(c: Checkin, patch: Partial<Checkin>): Checkin {
  Object.assign(c, patch);
  emit({ type: "checkin", checkin: structuredClone(c) });
  return c;
}

function caption(c: Checkin, speaker: "helper" | "her", text: string): void {
  if (c.status === "ended") return;
  emit({ type: "caption", checkin_id: c.id, speaker, text, final: true });
}

function addEvent(e: HearthEvent): void {
  S().events.unshift(e);
  emit({ type: "event", event: structuredClone(e) });
}

function addAlert(a: Alert): void {
  S().alerts.unshift(a);
  emit({ type: "alert", alert: structuredClone(a) });
}

function finish(c: Checkin, outcome: CheckinOutcome): void {
  const s = S();
  const mum = s.resident.family_name_for_her;
  const ts = now();
  const base = { checkin_id: c.id, source: c.trigger === "inbound" ? "inbound" : c.transport === "facetime" ? "facetime" : c.transport === "kiosk" ? "kiosk" : "mock" } as const;
  if (outcome === "confirmed") {
    const e =
      c.kind === "drink"
        ? ev(ts, "drink", `${mum} drank most of a glass`, "Held up the blue mug, drank twice, showed it again", { ...base, amount: "most", volume_ml_est: AMOUNT_ML.most, confidence: 0.9 })
        : c.kind === "meal"
          ? ev(ts, "meal", `${mum} ate some of her lunch`, "Showed her plate, took several bites", { ...base, amount: "some", confidence: 0.78 })
          : ev(ts, "med", `${mum} took her tablets`, "Opened the slot, tablet to mouth, sip of water, showed it empty", {
              ...base,
              med_steps: ["slot_opened", "pill_in_hand", "hand_to_mouth", "drank_water", "slot_shown_empty"],
              confidence: 0.86,
            });
    addEvent(e);
    updateCheckin(c, { outcome, summary: e.summary });
  } else if (outcome === "reported") {
    const said = { drink: "I've had my water, thank you", meal: "I've eaten, thank you", med: "I've taken my morning tablets, thank you" }[c.kind];
    const summary = { drink: "Said she had some water.", meal: "Said she has eaten.", med: "Said she took her morning tablets." }[c.kind];
    const e = ev(ts, "note", `${summary} The helper couldn't see her on this call.`, `Said: ${said}`, {
      ...base, for_kind: c.kind, said, confidence: 0.4,
    });
    addEvent(e);
    updateCheckin(c, { outcome, summary: e.summary });
  } else if (outcome === "refused") {
    const said = c.kind === "meal" ? "I'm not hungry yet" : "I'm not thirsty, I had tea";
    const e = ev(ts, "refused", `${mum} didn't want ${c.kind === "drink" ? "water" : c.kind === "meal" ? "to eat" : "her tablets"} just now`, "Said no kindly, seemed comfortable", { ...base, said, confidence: 0.9 });
    addEvent(e);
    addAlert({
      id: id("al"),
      ts: e.ts,
      resident_id: "res_margaret",
      checkin_id: c.id,
      event_id: e.id,
      kind: "refused",
      tier: "check",
      title: `${mum} didn't want ${c.kind === "drink" ? "her water" : c.kind === "meal" ? "to eat" : "her tablets"}`,
      detail: `She said ‘${said}’.`,
      said,
      status: "open",
      action: null,
      action_at: null,
      retry_at: null,
    });
    updateCheckin(c, { outcome, summary: e.summary });
  } else if (outcome === "concern") {
    const said = "I fell down, I can't get up";
    const e = ev(ts, "concern", `${mum} said she fell`, "She said she had fallen and couldn't get up; she was low in the picture", {
      ...base,
      said,
      concern_category: "said_fell_or_hurt",
      confidence: 0.8,
    });
    addEvent(e);
    addAlert({
      id: id("al"),
      ts: e.ts,
      resident_id: "res_margaret",
      checkin_id: c.id,
      event_id: e.id,
      kind: "concern",
      tier: "urgent",
      title: "Please check on her now",
      detail: `The check-in noticed something unusual: she said ‘${said}’ at ${formatTime(e.ts, tz)}. Please check on her now.`,
      said,
      status: "open",
      action: null,
      action_at: null,
      retry_at: null,
    });
    updateCheckin(c, { outcome, summary: e.summary });
  } else if (outcome === "no_answer") {
    const when = formatTime(c.created_at, tz);
    const e = ev(ts, "missed", `${mum} didn't answer her ${when} check-in`, "No answer after two tries", { ...base, confidence: 1 });
    addEvent(e);
    addAlert({
      id: id("al"),
      ts: e.ts,
      resident_id: "res_margaret",
      checkin_id: c.id,
      event_id: e.id,
      kind: "no_answer",
      tier: "check",
      title: `${mum} didn't answer`,
      detail: `${mum} didn't answer her ${when} check-in. Please check on her.`,
      said: null,
      status: "open",
      action: null,
      action_at: null,
      retry_at: null,
    });
    updateCheckin(c, { outcome, summary: e.summary });
  } else {
    updateCheckin(c, { outcome, summary: "The check-in couldn't finish." });
  }
}

function runScript(c: Checkin, outcome: CheckinOutcome): void {
  const s = S();
  const name = s.resident.preferred_name;
  const l = LINES[c.kind];
  const t = (ms: number, fn: () => void) => timers.push(setTimeout(() => c.status !== "ended" && fn(), ms));
  t(250, () => updateCheckin(c, { status: "ringing", ringing_at: now().toISOString() }));
  if (outcome === "no_answer") {
    t(5500, () => finish(c, "no_answer"));
    t(6000, () => updateCheckin(c, { status: "ended", ended_at: now().toISOString() }));
    return;
  }
  t(1300, () => updateCheckin(c, { status: "connected", connected_at: now().toISOString() }));
  t(1700, () => caption(c, "helper", `Hello ${name}, it's Sarah's helper. I'm an assistant.`));
  t(2900, () => caption(c, "her", "Oh, hello."));
  t(3500, () => caption(c, "helper", outcome === "reported" ? "I can't see you just now, but I can hear you." : l.ask));
  t(4300, () => updateCheckin(c, { status: "watching" }));
  if (outcome === "reported") {
    const task = { drink: "have some water", meal: "have your meal", med: "take your tablets" }[c.kind];
    t(4600, () => caption(c, "helper", `Please ${task} and tell me when you have.`));
    t(6300, () => caption(c, "her", { drink: "I've had my water, thank you", meal: "I've eaten, thank you", med: "I've taken my tablets, thank you" }[c.kind]));
    t(7600, () => caption(c, "helper", "Thank you. Goodbye for now."));
  } else if (outcome === "refused") {
    t(5300, () => caption(c, "her", c.kind === "meal" ? "I'm not hungry yet." : "I'm not thirsty, I had tea."));
    t(6600, () => caption(c, "helper", "That's all right. I'll let Sarah know."));
  } else if (outcome === "concern") {
    t(5300, () => caption(c, "her", "I fell down. I can't get up."));
    t(6600, () => caption(c, "helper", "I'm going to get Sarah for you right now. Stay where you are."));
  } else {
    t(5100, () => caption(c, "her", l.show));
    t(6300, () => caption(c, "helper", l.then));
    t(7600, () => caption(c, "helper", "Thank you. That's lovely."));
  }
  t(8400, () => finish(c, outcome));
  t(9000, () => updateCheckin(c, { status: "ended", ended_at: now().toISOString() }));
}

function dayStats(day: Date): DayStats {
  const s = S();
  const evs = s.events.filter((e) => sameDay(new Date(e.ts), day));
  return {
    day: `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`,
    fluid_ml: evs.filter((e) => e.kind === "drink").reduce((t, e) => t + (e.volume_ml_est ?? 0), 0),
    drinks_confirmed: evs.filter((e) => e.kind === "drink").length,
    drinks_refused: evs.filter((e) => e.kind === "refused").length,
    meals_confirmed: evs.filter((e) => e.kind === "meal").length,
    meds_confirmed: evs.filter((e) => e.kind === "med").length,
    meds_expected: 2,
    checkins_missed: evs.filter((e) => e.kind === "missed").length,
  };
}

function findAlert(alertId: string): Alert {
  const a = S().alerts.find((x) => x.id === alertId);
  if (!a) throw new ApiError(404, "That alert isn't there any more.");
  return a;
}

export const mockApi: HearthApi = {
  async health() {
    return delay({ ok: true, transport_default: "mock", integrations: { chert: "mock", gemini: "mock", tiger: "mock", typesense: "mock", push: "mock" } });
  },
  async login({ passcode }) {
    await delay(null, 250);
    if (passcode.trim() !== PASSCODE) throw new ApiError(401, "That passcode didn't work. Please check it and try again.");
    try {
      sessionStorage.setItem(SESSION_KEY, "1");
    } catch {
      /* private mode */
    }
    return delay<Me>({ voice: true, watch: watchEnabled(), name: S().resident.daughter_name, resident: S().resident });
  },
  async logout() {
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      /* ignore */
    }
  },
  async me() {
    requireSession();
    return delay<Me>({ voice: true, watch: watchEnabled(), name: S().resident.daughter_name, resident: S().resident });
  },
  async today() {
    requireSession();
    return delay(buildToday());
  },
  async route() {
    requireSession();
    return delay({ transport: "kiosk", reason: "kitchen screen is up", kitchen_online: true, platform: "ios" });
  },
  async watch() {
    requireSession();
    if (!watchEnabled()) return delay({ enabled: false, active: false, until: null, notes: [] });
    if (mockWatch.until && new Date(mockWatch.until) <= now()) mockWatch = { ...mockWatch, active: false, until: null };
    return delay({ ...mockWatch, enabled: true });
  },
  async startWatch({ minutes }) {
    requireSession();
    if (!watchEnabled()) throw new ApiError(404, "Watch mode is not enabled.");
    mockWatch = {
      enabled: true, active: true, until: new Date(now().getTime() + minutes * 60_000).toISOString(),
      notes: [{ ts: now().toISOString(), status: "ok", note: "Sitting at the table" }],
    };
    return delay(mockWatch);
  },
  async stopWatch() {
    requireSession();
    if (!watchEnabled()) throw new ApiError(404, "Watch mode is not enabled.");
    mockWatch = { ...mockWatch, active: false, until: null };
    return delay(mockWatch);
  },
  async week() {
    requireSession();
    const n = now();
    const days: DayStats[] = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date(n);
      d.setDate(d.getDate() - i);
      days.push(dayStats(d));
    }
    return delay({ days, fluid_target_ml: S().resident.fluid_target_ml, med_streak_days: 3, source: "memory" as const });
  },
  async events(q = {}) {
    requireSession();
    let list = S().events.filter((e) => new Date(e.ts) <= now());
    if (q.kind) list = list.filter((e) => e.kind === q.kind);
    if (q.since) list = list.filter((e) => e.ts >= q.since!);
    return delay(list.slice(0, q.limit ?? 200));
  },
  async addEvent(body) {
    requireSession();
    const e = ev(body.ts ? new Date(body.ts) : now(), body.kind, body.summary, "", {
      source: "manual",
      confidence: 1,
      amount: body.amount ?? null,
      volume_ml_est: body.kind === "drink" && body.amount ? AMOUNT_ML[body.amount] : null,
    });
    addEvent(e);
    return delay(e);
  },
  async startCheckin(body) {
    requireSession();
    const s = S();
    if (s.checkins.some((c) => c.status !== "ended")) throw new ApiError(409, "A check-in is already running. Please wait for it to finish.");
    const kind = body.kind ?? "drink";
    const c: Checkin = {
      id: id("ci"),
      resident_id: "res_margaret",
      kind,
      transport: body.transport === "auto" ? "mock" : body.transport ?? "mock",
      status: "queued",
      outcome: null,
      attempt: 1,
      trigger: "manual",
      schedule_item_id: null,
      cue_id: body.cue_id ?? s.cues.find((x) => x.kind === kind)?.id ?? null,
      room: `hearth-${Date.now()}`,
      created_at: now().toISOString(),
      ringing_at: null,
      connected_at: null,
      ended_at: null,
      summary: null,
      error: null,
    };
    s.checkins.push(c);
    emit({ type: "checkin", checkin: structuredClone(c) });
    runScript(c, body.mock_outcome ?? "confirmed");
    return delay(c, 60);
  },
  async checkin(cid) {
    requireSession();
    const c = S().checkins.find((x) => x.id === cid);
    if (!c) throw new ApiError(404, "That check-in isn't there.");
    return delay(c);
  },
  async cancelCheckin(cid) {
    requireSession();
    const c = S().checkins.find((x) => x.id === cid);
    if (!c) throw new ApiError(404, "That check-in isn't there.");
    if (c.status !== "ended") updateCheckin(c, { status: "ended", ended_at: now().toISOString(), summary: "You ended the check-in." });
    return delay(c);
  },
  async alerts(status) {
    requireSession();
    return delay(S().alerts.filter((a) => !status || a.status === status));
  },
  async alertAction(aid, { action, retry_minutes }) {
    requireSession();
    const a = findAlert(aid);
    const n = now();
    const patch: Partial<Alert> = { status: "handled", action: action as AlertAction, action_at: n.toISOString() };
    if (action === "retry_later") patch.retry_at = new Date(n.getTime() + (retry_minutes ?? 30) * 60_000).toISOString();
    Object.assign(a, patch);
    emit({ type: "alert", alert: structuredClone(a) });
    return delay(a);
  },
  async schedule() {
    requireSession();
    return delay(S().schedule);
  },
  async updateSchedule(sid, item) {
    requireSession();
    const s = S();
    s.schedule = s.schedule.map((x) => (x.id === sid ? { ...item, id: sid } : x));
    return delay({ ...item, id: sid });
  },
  async createSchedule(item) {
    requireSession();
    const full: ScheduleItem = { ...item, id: item.id ?? id("sch") };
    S().schedule.push(full);
    return delay(full);
  },
  async deleteSchedule(sid) {
    requireSession();
    const s = S();
    s.schedule = s.schedule.filter((x) => x.id !== sid);
  },
  async cues() {
    requireSession();
    return delay(S().cues);
  },
  async updateCue(cid, cue) {
    requireSession();
    const s = S();
    s.cues = s.cues.map((x) => (x.id === cid ? { ...cue, id: cid } : x));
    return delay({ ...cue, id: cid });
  },
  async uploadCuePhoto(cid, file) {
    requireSession();
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new ApiError(400, "Choose a JPEG, PNG or WebP photo.");
    if (file.size > 5 * 1024 * 1024) throw new ApiError(400, "That photo is too large. Choose one under 5 MB.");
    const c = S().cues.find((x) => x.id === cid);
    if (!c) throw new ApiError(404, "That cue isn't there.");
    const next = { ...c, image_url: URL.createObjectURL(file) };
    S().cues = S().cues.map((cue) => cue.id === cid ? next : cue);
    return delay(next);
  },
  async resident() {
    requireSession();
    return delay(S().resident);
  },
  async updateResident(r) {
    requireSession();
    S().resident = r;
    return delay(r);
  },
  async ask({ q = "", voice_wav_b64 }) {
    requireSession();
    if (voice_wav_b64 != null) q = "How much has she drunk today?";
    const question = q.toLowerCase();
    const words = question.split(/\W+/).filter((w) => w.length > 3);
    // Keep the practice suggestions useful without pretending this is conversational search.
    const kind = /say no|refus|didn.t want/.test(question) ? "refused"
      : /missed/.test(question) ? "missed"
      : /tablet|pill|medication/.test(question) ? "med"
      : /eat|ate|meal|lunch|breakfast|supper/.test(question) ? "meal"
      : /drink|drank|drunk|water|fluid/.test(question) ? "drink" : null;
    const current = now();
    const day = new Date(current);
    if (question.includes("yesterday")) day.setDate(day.getDate() - 1);
    const weekday = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"].findIndex((d) => question.includes(d));
    if (weekday >= 0) day.setDate(day.getDate() - (day.getDay() - weekday + 7) % 7);
    const weekStart = new Date(current);
    weekStart.setDate(weekStart.getDate() - (weekStart.getDay() + 6) % 7);
    weekStart.setHours(0, 0, 0, 0);
    const matched = S().events.filter((e) => {
      const time = new Date(e.ts);
      if (question.includes("today") || question.includes("yesterday") || weekday >= 0) {
        if (time.toDateString() !== day.toDateString()) return false;
      }
      if (question.includes("this week") && time < weekStart) return false;
      if (question.includes("evening") && time.getHours() < 16) return false;
      if (question.includes("lunch") && (time.getHours() < 11 || time.getHours() >= 15)) return false;
      return kind ? e.kind === kind : words.some((w) => `${e.summary} ${e.evidence} ${e.kind}`.toLowerCase().includes(w));
    }).sort((a, b) => b.ts.localeCompare(a.ts));
    const hits = matched.slice(0, 5).map((event) => ({ event, snippet: event.evidence || event.summary }));
    const answer = !kind && hits.length ? "Here are the closest moments I found."
      : /how much/.test(question) && kind === "drink" && hits.length
      ? `The helper observed about ${matched.reduce((sum, e) => sum + (e.volume_ml_est ?? 0), 0)} ml${question.includes("today") ? " today" : " in the matching moments"}.`
      : hits.length
      ? `Here's what the helper logged: ${hits[0].event.summary.toLowerCase()} at ${formatTime(hits[0].event.ts, tz)}.`
      : "I couldn't find anything about that in the log.";
    // Exercise the related-results presentation; practice mode still uses simple word matching.
    return delay({ q, answer, hits, engine: "fallback" as const, mode: kind ? "exact" as const : "related" as const }, 400);
  },
  async digest(day) {
    requireSession();
    const t = buildToday();
    return delay({
      day: day ?? t.now.slice(0, 10),
      text: `${t.resident.family_name_for_her} has had ${t.fluid_ml} ml so far, ate ${t.meals_confirmed} times and took ${t.meds_confirmed} of ${t.meds_expected} tablet doses.`,
      generated_by: "template",
    });
  },
  async privacy() {
    requireSession();
    const p: Privacy = {
      stored: [
        { what: "Check-in results", kept: true, detail: "What happened, when, and a short line about what the helper saw." },
        { what: "Your notes", kept: true, detail: "Anything you add yourself." },
        { what: "Video and audio", kept: false, detail: "Seen live during the call, never saved." },
      ],
      never_stored: ["Video", "Audio", "Photos from the call", "Full transcripts"],
      retention_days_events: 90,
      in_transit: ["FaceTime (Apple)", "Chert", "LiveKit", "Google Gemini"],
      consent: S().resident.consent,
      counts: { events: S().events.length, alerts: S().alerts.length },
    };
    return delay(p);
  },
  async deleteAllData() {
    requireSession();
    const n = S().events.length;
    S().events = [];
    S().alerts = [];
    return delay({ deleted_events: n });
  },
  async clock() {
    requireSession();
    return delay<Clock>({ now: now().toISOString(), offset_minutes: S().offsetMinutes });
  },
  async setClock({ advance_minutes, reset }) {
    requireSession();
    const s = S();
    if (reset) {
      s.offsetMinutes = 0;
      s.startNowMs = (() => {
        const d = new Date();
        d.setHours(14, 35, 0, 0);
        return d.getTime();
      })();
      s.startRealMs = Date.now();
    }
    if (advance_minutes) s.offsetMinutes += advance_minutes;
    const c: Clock = { now: now().toISOString(), offset_minutes: s.offsetMinutes };
    emit({ type: "clock", ...c });
    return delay(c);
  },
  async resetDemo() {
    requireSession();
    for (const t of timers.splice(0)) clearTimeout(t);
    state = seed();
    const t = buildToday();
    emit({ type: "clock", now: t.now, offset_minutes: 0 });
    return delay(t);
  },
  async pushKey() {
    requireSession();
    return delay({ public_key: null });
  },
  async pushSubscribe() {
    requireSession();
  },
  async wsTicket() {
    requireSession();
    return delay({ ticket: "mock" });
  },
  async kioskToken() {
    throw new ApiError(503, "The kitchen screen isn't available in practice mode.");
  },
};

/** The mock live channel: same messages the real /ws would send. */
export function subscribeMock(fn: Listener): () => void {
  listeners.add(fn);
  const active = S().checkins.find((c) => c.status !== "ended");
  if (active) setTimeout(() => fn({ type: "checkin", checkin: structuredClone(active) }), 0);
  setTimeout(() => fn({ type: "clock", now: now().toISOString(), offset_minutes: S().offsetMinutes }), 0);
  return () => listeners.delete(fn);
}
