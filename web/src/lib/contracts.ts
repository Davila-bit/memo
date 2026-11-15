// Mirror of contracts/hearth_contracts/models.py. Change both (and contracts/API.md) in ONE commit.
// Dates are ISO 8601 strings on the wire.

export type CheckinKind = "drink" | "meal" | "med";
export type Transport = "facetime" | "kiosk" | "mock";
export type CheckinStatus = "queued" | "ringing" | "connected" | "watching" | "ended";
// reported = she said she did it; not seen.
export type CheckinOutcome = "confirmed" | "reported" | "refused" | "no_answer" | "concern" | "error";
export type EventKind = "drink" | "meal" | "med" | "refused" | "missed" | "concern" | "note";
export type EventSource = "facetime" | "inbound" | "kiosk" | "manual" | "mock" | "seed";
export type Amount = "sip" | "some" | "most" | "all";
export type MedStep =
  | "slot_opened"
  | "pill_in_hand"
  | "hand_to_mouth"
  | "drank_water"
  | "slot_shown_empty";
export type ConcernCategory =
  | "said_fell_or_hurt"
  | "asked_for_help"
  | "reports_pain_or_unwell"
  | "not_responding"
  | "on_floor_or_out_of_view";
export type AlertKind = "refused" | "no_answer" | "concern";
export type AlertTier = "check" | "urgent";
export type AlertStatus = "open" | "handled";
export type AlertAction = "retry_later" | "i_will_call" | "ask_neighbour" | "handling" | "called_911";

export interface Consent {
  status: "signed" | "pending";
  signed_by: string;
  relation: "self" | "healthcare_agent" | "guardian";
  signed_at: string | null;
}

export interface Resident {
  id: string;
  name: string;
  preferred_name: string;
  family_name_for_her: string;
  daughter_name: string;
  phone: string | null;
  fluid_target_ml: number;
  timezone: string;
  quiet_hours: [string, string];
  consent: Consent;
}

export interface Cue {
  id: string;
  kind: CheckinKind;
  title: string;
  line: string;
  image_url: string | null;
  slot_label: string | null;
}

export interface ScheduleItem {
  id: string;
  resident_id: string;
  kind: CheckinKind;
  time: string; // "HH:MM"
  label: string;
  cue_id: string | null;
  enabled: boolean;
}

export interface Checkin {
  id: string;
  resident_id: string;
  kind: CheckinKind;
  transport: Transport;
  status: CheckinStatus;
  outcome: CheckinOutcome | null;
  attempt: number;
  trigger: "schedule" | "manual" | "retry" | "inbound";
  schedule_item_id: string | null;
  cue_id: string | null;
  room: string;
  created_at: string;
  ringing_at: string | null;
  connected_at: string | null;
  ended_at: string | null;
  summary: string | null;
  error: string | null;
}

export interface HearthEvent {
  id: string;
  ts: string;
  resident_id: string;
  checkin_id: string | null;
  kind: EventKind;
  for_kind: CheckinKind | null;
  source: EventSource;
  confidence: number; // 0..1
  summary: string;
  evidence: string;
  amount: Amount | null;
  volume_ml_est: number | null;
  med_steps: MedStep[] | null;
  said: string | null;
  concern_category: ConcernCategory | null;
}

export interface Alert {
  id: string;
  ts: string;
  resident_id: string;
  checkin_id: string | null;
  event_id: string | null;
  kind: AlertKind;
  tier: AlertTier;
  title: string;
  detail: string;
  said: string | null;
  status: AlertStatus;
  action: AlertAction | null;
  action_at: string | null;
  retry_at: string | null;
}

// ---- requests

export interface LoginRequest {
  passcode: string;
}

export interface Me {
  voice: boolean;
  name: string;
  resident: Resident;
}

export interface StartCheckinRequest {
  kind?: CheckinKind;
  transport?: Transport | null;
  cue_id?: string | null;
  mock_outcome?: CheckinOutcome | null;
}

export interface AlertActionRequest {
  action: AlertAction;
  retry_minutes?: number | null;
}

export interface ManualEventRequest {
  kind: "drink" | "meal" | "med" | "note";
  summary: string;
  amount?: Amount | null;
  ts?: string | null;
}

export interface AskRequest {
  q?: string;
  voice_wav_b64?: string | null;
}

export interface PushSubscription {
  endpoint: string;
  keys: Record<string, string>;
}

export interface ClockRequest {
  advance_minutes?: number | null;
  set_time?: string | null;
  reset?: boolean;
}

// ---- responses

export interface Today {
  now: string;
  resident: Resident;
  fluid_ml: number;
  fluid_target_ml: number;
  drinks_confirmed: number;
  meals_confirmed: number;
  meds_confirmed: number;
  meds_expected: number;
  next_checkin: ScheduleItem | null;
  next_checkin_at: string | null;
  active_checkin: Checkin | null;
  open_alerts: Alert[];
  events: HearthEvent[];
}

export interface DayStats {
  day: string;
  fluid_ml: number;
  drinks_confirmed: number;
  drinks_refused: number;
  meals_confirmed: number;
  meds_confirmed: number;
  meds_expected: number;
  checkins_missed: number;
}

export interface Week {
  days: DayStats[];
  fluid_target_ml: number;
  med_streak_days: number;
  source: "tiger_continuous_aggregate" | "memory";
}

export interface AskHit {
  event: HearthEvent;
  snippet: string;
}

export interface AskResponse {
  q: string;
  answer: string;
  hits: AskHit[];
  engine: "typesense" | "fallback";
  mode?: "exact" | "related" | null;
}

export interface Digest {
  day: string;
  text: string;
  generated_by: string;
}

export interface StoredThing {
  what: string;
  kept: boolean;
  detail: string;
}

export interface Privacy {
  stored: StoredThing[];
  never_stored: string[];
  retention_days_events: number;
  in_transit: string[];
  consent: Consent;
  counts: Record<string, number>;
}

export interface Clock {
  now: string;
  offset_minutes: number;
}

export interface KioskToken {
  url: string;
  token: string;
  room: string;
  identity: string;
}

/** LiveKit dispatch metadata, shared with the API and helper. */
export interface AgentJob {
  checkin_id: string;
  kind: CheckinKind;
  transport: Transport;
  inbound?: boolean; // defaults to false
  resident: Resident;
  cue: Cue | null;
  api_base: string;
  caller_identity_prefix: string;
  max_seconds: number;
  context: string;
}

export interface Health {
  ok: boolean;
  transport_default: Transport;
  integrations: Record<string, "ok" | "missing_key" | "error" | "mock">;
}

// ---- live channel (/ws)

export type WsMessage =
  | { type: "checkin"; checkin: Checkin }
  | { type: "event"; event: HearthEvent }
  | { type: "alert"; alert: Alert }
  | { type: "caption"; checkin_id: string; speaker: "helper" | "her"; text: string; final: boolean }
  | { type: "clock"; now: string; offset_minutes: number };

export const AMOUNT_ML: Record<Amount, number> = { sip: 30, some: 80, most: 180, all: 240 };
