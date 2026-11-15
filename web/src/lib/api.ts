// Typed wrappers for every REST route in contracts/API.md §4.
// In mock mode (NEXT_PUBLIC_HEARTH_MOCK=1) the same interface is served by an in-browser fake.
import type {
  Alert,
  AlertActionRequest,
  AskRequest,
  AskResponse,
  Checkin,
  Clock,
  ClockRequest,
  Cue,
  Digest,
  EventKind,
  Health,
  HearthEvent,
  KioskToken,
  LoginRequest,
  ManualEventRequest,
  Me,
  Privacy,
  Resident,
  ScheduleItem,
  StartCheckinRequest,
  Today,
  Watch,
  WatchRequest,
  Week,
} from "./contracts";
import { ApiError, notifyUnauthorized } from "./api-error";
import { IS_MOCK } from "./env";
import { mockApi } from "./mock";
import { withBase } from "./base";

export { ApiError, onUnauthorized } from "./api-error";

/** Browser PushSubscription.toJSON() shape, which is what /api/push/subscribe takes. */
export interface PushSubscriptionBody {
  endpoint: string;
  expirationTime?: number | null;
  keys: { p256dh: string; auth: string };
}

export interface HearthApi {
  health(): Promise<Health>;
  login(body: LoginRequest): Promise<Me>;
  logout(): Promise<void>;
  me(): Promise<Me>;
  today(): Promise<Today>;
  watch(): Promise<Watch>;
  startWatch(body: WatchRequest): Promise<Watch>;
  stopWatch(): Promise<Watch>;
  week(): Promise<Week>;
  events(q?: { kind?: EventKind; since?: string; limit?: number }): Promise<HearthEvent[]>;
  addEvent(body: ManualEventRequest): Promise<HearthEvent>;
  startCheckin(body: StartCheckinRequest): Promise<Checkin>;
  checkin(id: string): Promise<Checkin>;
  cancelCheckin(id: string): Promise<Checkin>;
  alerts(status?: "open" | "handled"): Promise<Alert[]>;
  alertAction(id: string, body: AlertActionRequest): Promise<Alert>;
  schedule(): Promise<ScheduleItem[]>;
  updateSchedule(id: string, item: ScheduleItem): Promise<ScheduleItem>;
  createSchedule(item: Omit<ScheduleItem, "id"> & { id?: string }): Promise<ScheduleItem>;
  deleteSchedule(id: string): Promise<void>;
  cues(): Promise<Cue[]>;
  updateCue(id: string, cue: Cue): Promise<Cue>;
  uploadCuePhoto(id: string, file: File): Promise<Cue>;
  resident(): Promise<Resident>;
  updateResident(r: Resident): Promise<Resident>;
  ask(body: AskRequest): Promise<AskResponse>;
  digest(day?: string): Promise<Digest>;
  privacy(): Promise<Privacy>;
  deleteAllData(): Promise<{ deleted_events: number }>;
  clock(): Promise<Clock>;
  setClock(body: ClockRequest): Promise<Clock>;
  resetDemo(): Promise<Today>;
  pushKey(): Promise<{ public_key: string | null }>;
  pushSubscribe(sub: PushSubscriptionBody): Promise<void>;
  wsTicket(): Promise<{ ticket: string }>;
  kioskToken(device: string, key: string): Promise<KioskToken>;
}

async function request<T>(method: string, path: string, body?: unknown, opts: { auth?: boolean } = {}): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(withBase(path), init);
  } catch {
    throw new ApiError(0, "Couldn't reach Memo.");
  }
  if (res.status === 401 && opts.auth !== false) notifyUnauthorized();
  if (!res.ok) {
    let detail = res.status === 401 ? "Please sign in again." : "Something went wrong. Please try again.";
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

function qs(q: Record<string, string | number | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

const realApi: HearthApi = {
  health: () => request("GET", "/api/health", undefined, { auth: false }),
  login: (body) => request("POST", "/api/auth/login", body, { auth: false }),
  logout: () => request("POST", "/api/auth/logout", undefined, { auth: false }),
  me: () => request("GET", "/api/me"),
  today: () => request("GET", "/api/today"),
  watch: () => request("GET", "/api/watch"),
  startWatch: (body) => request("POST", "/api/watch", body),
  stopWatch: () => request("DELETE", "/api/watch"),
  week: () => request("GET", "/api/week"),
  events: (q = {}) => request("GET", `/api/events${qs(q)}`),
  addEvent: (body) => request("POST", "/api/events", body),
  startCheckin: (body) => request("POST", "/api/checkins", body),
  checkin: (id) => request("GET", `/api/checkins/${encodeURIComponent(id)}`),
  cancelCheckin: (id) => request("POST", `/api/checkins/${encodeURIComponent(id)}/cancel`),
  alerts: (status) => request("GET", `/api/alerts${qs({ status })}`),
  alertAction: (id, body) => request("POST", `/api/alerts/${encodeURIComponent(id)}/action`, body),
  schedule: () => request("GET", "/api/schedule"),
  updateSchedule: (id, item) => request("PUT", `/api/schedule/${encodeURIComponent(id)}`, item),
  createSchedule: (item) => request("POST", "/api/schedule", item),
  deleteSchedule: (id) => request("DELETE", `/api/schedule/${encodeURIComponent(id)}`),
  cues: () => request("GET", "/api/cues"),
  updateCue: (id, cue) => request("PUT", `/api/cues/${encodeURIComponent(id)}`, cue),
  uploadCuePhoto: (id, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request("POST", `/api/cues/${encodeURIComponent(id)}/photo`, fd);
  },
  resident: () => request("GET", "/api/resident"),
  updateResident: (r) => request("PUT", "/api/resident", r),
  ask: (body) => request("POST", "/api/ask", body),
  digest: (day) => request("GET", `/api/digest${qs({ day })}`),
  privacy: () => request("GET", "/api/privacy"),
  deleteAllData: () => request("DELETE", "/api/privacy/data"),
  clock: () => request("GET", "/api/demo/clock"),
  setClock: (body) => request("POST", "/api/demo/clock", body),
  resetDemo: () => request("POST", "/api/demo/reset"),
  pushKey: () => request("GET", "/api/push/key"),
  pushSubscribe: (sub) => request("POST", "/api/push/subscribe", sub),
  wsTicket: () => request("GET", "/api/ws-ticket"),
  kioskToken: (device, key) => request("GET", `/api/kiosk/token${qs({ device, key })}`, undefined, { auth: false }),
};

export const api: HearthApi = IS_MOCK ? mockApi : realApi;
