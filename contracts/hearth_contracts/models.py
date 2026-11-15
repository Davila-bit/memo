"""Memo shared data contracts (API <-> web <-> agent).

This file is the reference. `web/src/lib/contracts.ts` mirrors it by hand. Change both, plus
`contracts/API.md`, together in one commit so the API, web and agent share the same contracts.

Privacy rule baked into the shapes: nothing here can carry call audio, video or frames. Evidence is
short text. Captions are live-only (WebSocket) and are never stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------- vocab

CheckinKind = Literal["drink", "meal", "med"]
Transport = Literal["facetime", "kiosk", "mock"]

# Lifecycle of one check-in call. Always moves forward; `ended` is terminal.
CheckinStatus = Literal[
    "queued",     # row created, room + agent being prepared
    "ringing",    # call placed (FaceTime dialling / kiosk being joined)
    "connected",  # she answered; media is flowing; agent may greet
    "watching",   # agent has asked and is watching for the action
    "ended",      # terminal; see `outcome`
]

CheckinOutcome = Literal[
    "confirmed",  # observed taking (drink / meal / med)
    "reported",   # she said she did it; not seen
    "refused",    # she declined or did not do it after two kind asks
    "no_answer",  # nobody answered / no face within 60 s
    "concern",    # safety concern flagged; daughter alerted urgently
    "error",      # technical failure (call failed, agent crashed, 90 s budget hit with no result)
]

EventKind = Literal["drink", "meal", "med", "refused", "missed", "concern", "note"]
EventSource = Literal["facetime", "inbound", "kiosk", "manual", "mock", "seed"]

Amount = Literal["sip", "some", "most", "all"]

MedStep = Literal["slot_opened", "pill_in_hand", "hand_to_mouth", "drank_water", "slot_shown_empty"]

ConcernCategory = Literal[
    "said_fell_or_hurt",
    "asked_for_help",
    "reports_pain_or_unwell",
    "not_responding",
    "on_floor_or_out_of_view",
]

AlertKind = Literal["refused", "no_answer", "concern"]
AlertTier = Literal["check", "urgent"]  # check = amber, urgent = coral + sound
AlertStatus = Literal["open", "handled"]
AlertAction = Literal[
    "retry_later",    # "Try again at 3" (uses retry_minutes)
    "i_will_call",    # "I'll call her"
    "ask_neighbour",  # "Ask a neighbour"
    "handling",       # "I'm handling it" (concern)
    "called_911",     # the daughter tapped the tel: link herself; the agent never dials
]

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------- people & setup


class Consent(Model):
    status: Literal["signed", "pending"]
    signed_by: str  # "Margaret Ellis" or her healthcare agent
    relation: Literal["self", "healthcare_agent", "guardian"]
    signed_at: datetime | None = None


class Resident(Model):
    id: str  # "margaret"
    name: str  # "Margaret Ellis"
    preferred_name: str  # what the agent calls her: "Margaret"
    family_name_for_her: str  # what the daughter calls her in the app: "Mum"
    daughter_name: str  # "Sarah" (the agent says "Sarah's helper")
    phone: str | None = None  # her phone, for the daughter's "I'll call her" link
    fluid_target_ml: int = 1500
    timezone: str = "America/New_York"
    quiet_hours: tuple[str, str] = ("21:00", "07:00")  # no scheduled check-ins inside
    consent: Consent


class Cue(Model):
    """What the agent shows on its video: her own mug, her plate, the pill-box slot."""

    id: str
    kind: CheckinKind
    title: str  # "Your blue mug"
    line: str  # one very large line on the card: "Time for some water, Margaret"
    image_url: str | None = None  # served by the API under /media/cues/...; a family photo of the object
    slot_label: str | None = None  # meds only: "TUE · MORNING"


class ScheduleItem(Model):
    id: str
    resident_id: str
    kind: CheckinKind
    time: str  # "HH:MM" local to the resident
    label: str  # "Morning water"
    cue_id: str | None = None
    enabled: bool = True


# ---------------------------------------------------------------------------- check-ins & events


class Checkin(Model):
    id: str
    resident_id: str
    kind: CheckinKind
    transport: Transport
    status: CheckinStatus
    outcome: CheckinOutcome | None = None
    attempt: int = 1  # 2 = the automatic retry after a no-answer
    trigger: Literal["schedule", "manual", "retry", "inbound"] = "manual"
    schedule_item_id: str | None = None
    cue_id: str | None = None
    room: str  # LiveKit room name, "checkin-<id>" (kiosk: "kiosk-<device>")
    created_at: datetime
    ringing_at: datetime | None = None
    connected_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str | None = None  # one human sentence once ended: "Drank most of a glass of water"
    error: str | None = None  # short technical reason when outcome == "error"


class HearthEvent(Model):
    """One row in the `events` hypertable. The only durable record of a check-in."""

    id: str
    ts: datetime
    resident_id: str
    checkin_id: str | None = None
    kind: EventKind
    for_kind: CheckinKind | None = None  # what a refusal / miss / concern / note was for
    source: EventSource
    confidence: Confidence = 1.0
    summary: str  # human sentence for the timeline: "Drank most of a glass of water"
    evidence: str = ""  # short, PII-redacted: "Held the blue mug up, drank twice, mug lower after"
    amount: Amount | None = None  # drink / meal
    volume_ml_est: int | None = None  # drink only; derived from amount (sip 30, some 80, most 180, all 240)
    med_steps: list[MedStep] | None = None  # med only: the steps that were seen
    said: str | None = None  # refused / concern: short gist or quote of what she said (redacted)
    concern_category: ConcernCategory | None = None


class Alert(Model):
    """Something the daughter must look at. Never auto-dismissed."""

    id: str
    ts: datetime
    resident_id: str
    checkin_id: str | None = None
    event_id: str | None = None
    kind: AlertKind
    tier: AlertTier
    title: str  # "Mum didn't want her 2:00 water"
    detail: str  # "She said: not thirsty, had tea. Asked twice, kindly."
    said: str | None = None
    status: AlertStatus = "open"
    action: AlertAction | None = None
    action_at: datetime | None = None
    retry_at: datetime | None = None


# ---------------------------------------------------------------------------- requests


class LoginRequest(Model):
    passcode: str


class Me(Model):
    voice: bool = False  # search can accept voice questions now
    watch: bool = False  # HEARTH_WATCH=1 only
    name: str  # "Sarah"
    resident: Resident


class WatchNote(Model):
    ts: datetime
    status: Literal["ok", "unusual", "concern"]
    note: str = Field(max_length=200)


class Watch(Model):
    enabled: bool
    active: bool
    until: datetime | None = None
    notes: list[WatchNote] = Field(default_factory=list)


class WatchRequest(Model):
    minutes: Literal[30, 120]


class StartCheckinRequest(Model):
    kind: CheckinKind = "drink"
    transport: Transport | None = None  # None = server default (HEARTH_TRANSPORT)
    cue_id: str | None = None
    mock_outcome: CheckinOutcome | None = None  # mock only: confirmed/reported/refused/no_answer/concern/error


class AlertActionRequest(Model):
    action: AlertAction
    retry_minutes: int | None = Field(default=None, ge=1, le=240)


class ManualEventRequest(Model):
    """The daughter logs something herself ("I gave her tea when I visited")."""

    kind: Literal["drink", "meal", "med", "note"]
    summary: str = Field(max_length=200)
    amount: Amount | None = None
    ts: datetime | None = None


class AskRequest(Model):
    q: str = Field(default="", max_length=300)
    voice_wav_b64: str | None = None


class ClockRequest(Model):
    """Demo clock. Exactly one of the fields."""

    advance_minutes: int | None = None
    set_time: str | None = None  # "HH:MM" today
    reset: bool = False


class PushSubscription(Model):
    endpoint: str
    keys: dict[str, str]


# ---------------------------------------------------------------------------- responses


class Today(Model):
    now: datetime  # demo clock
    resident: Resident
    fluid_ml: int
    fluid_target_ml: int
    drinks_confirmed: int
    meals_confirmed: int
    meds_confirmed: int
    meds_expected: int
    next_checkin: ScheduleItem | None
    next_checkin_at: datetime | None
    active_checkin: Checkin | None
    open_alerts: list[Alert]
    events: list[HearthEvent]  # today, newest first


class DayStats(Model):
    day: str  # "2026-11-14"
    fluid_ml: int
    drinks_confirmed: int
    drinks_refused: int
    meals_confirmed: int
    meds_confirmed: int
    meds_expected: int
    checkins_missed: int


class Week(Model):
    days: list[DayStats]  # oldest first, 7 entries
    fluid_target_ml: int
    med_streak_days: int
    source: Literal["tiger_continuous_aggregate", "memory"]  # shown in the UI footnote


class AskHit(Model):
    event: HearthEvent
    snippet: str


class AskResponse(Model):
    q: str
    answer: str  # one or two plain sentences; "I couldn't find that" when unsure
    hits: list[AskHit]
    engine: Literal["typesense", "fallback"]
    mode: Literal["exact", "related"] | None = None


class Digest(Model):
    day: str
    text: str  # 3 short sentences, warm and plain
    generated_by: str  # model id, or "template" when no model was available


class StoredThing(Model):
    what: str  # "Check-in events"
    kept: bool
    detail: str  # "Time, kind, a short description. Deleted after 90 days."


class Privacy(Model):
    stored: list[StoredThing]
    never_stored: list[str]  # "Video or audio of any call", ...
    retention_days_events: int
    in_transit: list[str]  # who sees live media: "Chert (FaceTime bridge)", "LiveKit Cloud", "Google Gemini"
    consent: Consent
    counts: dict[str, int]  # {"events": 41, "frames_stored": 0, "recordings": 0}


class Clock(Model):
    now: datetime
    offset_minutes: int


class KioskToken(Model):
    url: str  # LiveKit wss URL
    token: str
    room: str
    identity: str  # "kiosk-<device>"


class Health(Model):
    ok: bool
    transport_default: Transport
    integrations: dict[str, Literal["ok", "missing_key", "error", "mock"]]


# ---------------------------------------------------------------------------- live (WebSocket /ws)


class WsCheckin(Model):
    type: Literal["checkin"] = "checkin"
    checkin: Checkin


class WsEvent(Model):
    type: Literal["event"] = "event"
    event: HearthEvent


class WsAlert(Model):
    type: Literal["alert"] = "alert"
    alert: Alert


class WsCaption(Model):
    """Live-only. Never written to the database or a log."""

    type: Literal["caption"] = "caption"
    checkin_id: str
    speaker: Literal["helper", "her"]
    text: str
    final: bool = True


class WsClock(Model):
    type: Literal["clock"] = "clock"
    now: datetime
    offset_minutes: int


WsMessage = Annotated[WsCheckin | WsEvent | WsAlert | WsCaption | WsClock, Field(discriminator="type")]


# ---------------------------------------------------------------------------- agent <-> API (internal)


class AgentJob(Model):
    """JSON in the LiveKit agent dispatch metadata. Everything the agent needs for one check-in."""

    checkin_id: str
    kind: CheckinKind
    transport: Transport
    inbound: bool = False
    resident: Resident
    cue: Cue | None = None
    api_base: str  # "http://127.0.0.1:8000"
    caller_identity_prefix: str  # "chert-bridge-" or "kiosk-": whose audio/video is "her"
    max_seconds: int = 90
    context: str = ""  # one line: "She has had 2 drinks today (about 320 ml). Last drink 11:05."


class AgentStatus(Model):
    """POST /internal/checkins/{id}/status"""

    status: Literal["connected", "watching"]


class ToolConfirmDrink(Model):
    amount: Amount
    confidence: Confidence
    evidence: str = Field(max_length=240)  # what was SEEN, never "swallowed"


class ToolConfirmMeal(Model):
    amount: Amount
    confidence: Confidence
    evidence: str = Field(max_length=240)


class ToolConfirmMed(Model):
    steps_seen: list[MedStep]
    confidence: Confidence
    evidence: str = Field(max_length=240)


class ToolReportRefusal(Model):
    said: str = Field(max_length=200)  # gist of what she said: "not thirsty, had tea"
    asked_times: int = Field(ge=1, le=2)


class ToolFlagConcern(Model):
    category: ConcernCategory
    evidence_quote: str = Field(max_length=200)  # her words, or what was seen
    detected_by: Literal["model", "keyword"] = "model"


class ToolEndCheckin(Model):
    reason: Literal["done", "refused", "no_answer", "concern", "distressed", "time_budget", "error"]
    detail: str = Field(default="", max_length=200)


class AgentCaption(Model):
    speaker: Literal["helper", "her"]
    text: str = Field(max_length=400)
    final: bool = True


class ToolResult(Model):
    """What every /internal tool endpoint returns; `say` is a hint the agent may speak."""

    ok: bool = True
    event_id: str | None = None
    say: str | None = None


AMOUNT_ML: dict[str, int] = {"sip": 30, "some": 80, "most": 180, "all": 240}
