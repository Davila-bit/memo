"""CheckinService: the check-in lifecycle (contracts/API.md §7), agent tools (§6), alerts,
no-show chain, schedule tick, and the Today / Week views. Implements ports.CallCallbacks."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from hearth_contracts import (
    AMOUNT_ML,
    AgentCaption,
    AgentJob,
    AgentStatus,
    Alert,
    AlertActionRequest,
    Checkin,
    Cue,
    DayStats,
    HearthEvent,
    ManualEventRequest,
    Resident,
    ScheduleItem,
    StartCheckinRequest,
    Today,
    ToolConfirmDrink,
    ToolConfirmMeal,
    ToolConfirmMed,
    ToolEndCheckin,
    ToolFlagConcern,
    ToolReportRefusal,
    ToolResult,
    Week,
    WsAlert,
    WsCaption,
    WsCheckin,
    WsEvent,
)

from . import wording
from .clock import Bus, DemoClock, at_local, fmt_short, fmt_time, in_quiet_hours, local_day_bounds
from .settings import Settings
from .store import MemoryStore, Store

log = logging.getLogger("hearth.core")

LOW_CONFIDENCE = 0.5
SCHEDULE_GRACE = timedelta(minutes=10)
STATUS_ORDER = {"queued": 0, "ringing": 1, "connected": 2, "watching": 3, "ended": 4}
REFUSAL_ACTIONS = [("retry_later", "Try again later"), ("i_will_call", "I'll call her"),
                   ("ask_neighbour", "Ask a neighbour")]
CONCERN_ACTIONS = [("handling", "I'm handling it"), ("called_911", "Call 911")]
SPOKEN_CONCERN = {"said_fell_or_hurt", "asked_for_help", "reports_pain_or_unwell"}
END_REASON_OUTCOME = {"refused": "refused", "no_answer": "no_answer", "concern": "concern",
                      "time_budget": "error", "error": "error"}


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class CheckinService:
    def __init__(self, *, store: Store, clock: DemoClock, bus: Bus, settings: Settings, notifier: Any,
                 search: Any, digest: Any, redactor: Any, real_calls: Any | None, mock_calls: Any) -> None:
        self.store = store
        self.clock = clock
        self.bus = bus
        self.settings = settings
        self.notifier = notifier
        self.search = search
        self.digest = digest
        self.redactor = redactor
        self.real_calls = real_calls
        self.mock_calls = mock_calls
        self.lock = asyncio.Lock()
        self.services: dict[str, Any] = {}  # checkin_id -> CallService that runs it
        self.retry_origin: dict[str, datetime] = {}  # retry checkin_id -> when attempt 1 was due
        # How a check-in we are ending ourselves should finish, whatever the call layer reports.
        self.ending: dict[str, tuple[str | None, str | None, bool]] = {}
        self.mock_requested: dict[str, str] = {}  # checkin_id -> mock_outcome, so a retry replays it
        self.tasks: set[asyncio.Task] = set()
        self._last_tick: datetime | None = None

    # ------------------------------------------------------------------ helpers
    def _bg(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    async def shutdown(self) -> None:
        for t in list(self.tasks):
            t.cancel()

    async def resident(self) -> Resident:
        r = await self.store.get_resident()
        if r is None:
            raise NotFound("No resident is set up yet")
        return r

    def slot_label(self, when: datetime, tz: str) -> str:
        t = when.astimezone(ZoneInfo(tz))
        return f"{t.strftime('%a').upper()} · {'MORNING' if t.hour < 14 else 'EVENING'}"

    def with_slot(self, cue: Cue, r: Resident) -> Cue:
        if cue.kind == "med":
            return cue.model_copy(update={"slot_label": self.slot_label(self.clock.now(), r.timezone)})
        return cue

    async def cues(self) -> list[Cue]:
        r = await self.resident()
        return [self.with_slot(c, r) for c in await self.store.list_cues()]

    async def _cue_for(self, kind: str, cue_id: str | None, r: Resident) -> Cue | None:
        cue = await self.store.get_cue(cue_id) if cue_id else None
        if cue is None:
            cue = next((c for c in await self.store.list_cues() if c.kind == kind), None)
        return self.with_slot(cue, r) if cue else None

    async def _publish(self, msg) -> None:
        try:
            await self.bus.publish(msg)
        except Exception:
            log.exception("bus publish failed")

    async def _notify(self, title: str, body: str, *, urgent: bool = False, url: str | None = None,
                      actions: list[tuple[str, str]] | None = None) -> None:
        try:
            await self.notifier.notify(title, body, urgent=urgent, url=url, actions=actions)
        except Exception as e:  # noqa: BLE001
            log.warning("notifier failed: %s", e)

    def _redact(self, text: str | None) -> str | None:
        if not text:
            return text
        try:
            return self.redactor.redact(text)
        except Exception as e:  # noqa: BLE001
            log.warning("redactor failed (%s); dropping the text", type(e).__name__)
            return "[removed]"

    async def add_event(self, ev: HearthEvent, checkin_kind: str | None) -> HearthEvent:
        await self.store.add_event(ev, checkin_kind)
        await self._publish(WsEvent(event=ev))
        try:
            await self.search.index(ev)
        except Exception as e:  # noqa: BLE001
            log.warning("search index failed: %s", e)
        return ev

    async def reindex(self) -> None:
        """Send stored events (e.g. the seed history) to Search so "ask the log" can find them."""
        n = 0
        for ev in await self.store.list_events(limit=2000):
            try:
                await self.search.index(ev)
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("search reindex stopped after %d events: %s", n, e)
                return
        log.info("search: indexed %d stored events", n)

    async def _save_checkin(self, c: Checkin) -> Checkin:
        await self.store.put_checkin(c)
        await self._publish(WsCheckin(checkin=c))
        return c

    async def _save_alert(self, a: Alert) -> Alert:
        await self.store.put_alert(a)
        await self._publish(WsAlert(alert=a))
        return a

    async def _get(self, checkin_id: str) -> Checkin:
        c = await self.store.get_checkin(checkin_id)
        if c is None:
            raise NotFound("That check-in doesn't exist")
        return c

    async def _due(self, c: Checkin, r: Resident) -> datetime:
        """When this check-in was due: the schedule time ("her 2:00 water"), else when it started."""
        first = self.retry_origin.get(c.id, c.created_at)
        if c.schedule_item_id:
            item = await self.store.get_schedule_item(c.schedule_item_id)
            if item:
                return at_local(first.astimezone(ZoneInfo(r.timezone)).date(), item.time, r.timezone)
        return first

    async def _label_for(self, c: Checkin) -> str | None:
        if c.schedule_item_id:
            item = await self.store.get_schedule_item(c.schedule_item_id)
            if item:
                return item.label
        return None

    # ------------------------------------------------------------------ start
    def _pick_service(self, transport: str) -> tuple[str, Any]:
        if transport == "mock" or self.real_calls is None:
            if transport != "mock":
                log.info("no call module loaded; %s check-in runs on the mock transport", transport)
            return "mock", self.mock_calls
        try:
            h = self.real_calls.health() or {}
        except Exception as e:  # noqa: BLE001
            log.warning("call service health failed: %s", e)
            h = {}
        livekit_ok = h.get("livekit") == "ok"
        if transport == "facetime":
            if livekit_ok and h.get("chert") == "ok":
                return "facetime", self.real_calls
            transport = "kiosk"
            log.info("FaceTime not available (%s); trying the kiosk", h)
        if transport == "kiosk" and livekit_ok:
            return "kiosk", self.real_calls
        log.info("real call transport unavailable (%s); using the mock transport", h)
        return "mock", self.mock_calls

    async def context_line(self, r: Resident) -> str:
        start, end = local_day_bounds(self.clock.now().astimezone(ZoneInfo(r.timezone)).date(), r.timezone)
        evs = await self.store.list_events(since=start, until=end, limit=500)
        drinks = [e for e in evs if e.kind == "drink"]
        ml = sum(e.volume_ml_est or 0 for e in drinks)
        if not drinks:
            return "She hasn't had a drink on a check-in yet today."
        last = fmt_time(drinks[0].ts, r.timezone)
        return (f"She has had {len(drinks)} drink{'s' if len(drinks) != 1 else ''} today (about {ml} ml). "
                f"Last drink {last}.")

    async def start(self, req: StartCheckinRequest, *, trigger: str = "manual", schedule_item_id: str | None = None,
                    attempt: int = 1) -> Checkin:
        async with self.lock:
            if await self.store.active_checkin() is not None:
                raise Conflict("A check-in is already running. Please wait for it to finish.")
            r = await self.resident()
            transport, service = self._pick_service(req.transport or self.settings.transport_default)
            cue = await self._cue_for(req.kind, req.cue_id, r)
            cid = new_id("chk")
            room = f"kiosk-{self.settings.kiosk_device}" if transport == "kiosk" else f"checkin-{cid}"
            c = Checkin(id=cid, resident_id=r.id, kind=req.kind, transport=transport, status="queued",
                        attempt=attempt, trigger=trigger, schedule_item_id=schedule_item_id,
                        cue_id=cue.id if cue else None, room=room, created_at=self.clock.now())
            await self._save_checkin(c)
            self.services[cid] = service
        job = AgentJob(
            checkin_id=cid, kind=req.kind, transport=transport, resident=r, cue=cue,
            api_base=self.settings.api_base,
            caller_identity_prefix={"facetime": "chert-bridge-", "kiosk": "kiosk-"}.get(transport, "mock-"),
            max_seconds=self.settings.max_call_seconds, context=await self.context_line(r))
        if service is self.mock_calls and req.mock_outcome:
            self.mock_calls.outcomes[cid] = req.mock_outcome
            self.mock_requested[cid] = req.mock_outcome
        try:
            await service.start(c, job, self)
        except Exception as e:  # noqa: BLE001
            log.warning("call start failed: %s", e)
            await self.call_finished(cid, "error", f"The call couldn't start: {type(e).__name__}")
        return await self._get(cid)

    # ------------------------------------------------------------------ CallCallbacks
    async def _advance(self, checkin_id: str, status: str) -> Checkin | None:
        async with self.lock:
            c = await self.store.get_checkin(checkin_id)
            if c is None or STATUS_ORDER[status] <= STATUS_ORDER[c.status]:
                return c
            upd: dict[str, Any] = {"status": status}
            if status == "ringing":
                upd["ringing_at"] = self.clock.now()
            if status == "connected" or (status == "watching" and c.connected_at is None):
                upd["connected_at"] = self.clock.now()
            c = c.model_copy(update=upd)
            return await self._save_checkin(c)

    async def call_ringing(self, checkin_id: str) -> None:
        await self._advance(checkin_id, "ringing")

    async def call_connected(self, checkin_id: str) -> None:
        await self._advance(checkin_id, "connected")

    async def agent_status(self, checkin_id: str, body: AgentStatus) -> ToolResult:
        await self._get(checkin_id)
        await self._advance(checkin_id, body.status)
        return ToolResult(ok=True)

    async def caption(self, checkin_id: str, body: AgentCaption) -> None:
        # Live-only: never stored, never logged.
        await self._publish(WsCaption(checkin_id=checkin_id, speaker=body.speaker, text=body.text, final=body.final))

    async def call_finished(self, checkin_id: str, outcome: str | None, error: str | None) -> None:
        await self._finish(checkin_id, outcome, error)

    async def _finish(self, checkin_id: str, outcome: str | None, error: str | None, *,
                      cancelled: bool = False) -> None:
        if checkin_id in self.ending:
            outcome, error, cancelled = self.ending.pop(checkin_id)
        async with self.lock:
            c = await self.store.get_checkin(checkin_id)
            if c is None or c.status == "ended":
                return
            self.services.pop(checkin_id, None)
            r = await self.resident()
            now = self.clock.now()
            final = c.outcome or (None if cancelled else (outcome or "error"))
            summary = c.summary
            what = wording.what_for(c.kind, await self._label_for(c))
            due = await self._due(c, r)
            event: HearthEvent | None = None
            alert: Alert | None = None
            retry = False
            if cancelled and not c.outcome:
                summary = "Check-in stopped by Sarah"
            elif final == "no_answer":
                if c.attempt < 2:
                    retry = True
                    mins = max(1, round(self.settings.retry_seconds / 60))
                    summary = f"No answer. Trying again in {mins} minute{'s' if mins != 1 else ''}"
                else:
                    summary = f"Didn't answer the {fmt_short(due, r.timezone)} check-in (tried twice)"
                    event = HearthEvent(id=new_id("ev"), ts=now, resident_id=r.id, checkin_id=c.id, kind="missed",
                                        source=c.transport, summary=summary)
                    alert = Alert(
                        id=new_id("al"), ts=now, resident_id=r.id, checkin_id=c.id, event_id=event.id,
                        kind="no_answer", tier="check",
                        title=f"{r.family_name_for_her} didn't answer her {fmt_short(due, r.timezone)} check-in",
                        detail="Tried twice, a couple of minutes apart. Please check on her.")
            elif final == "error" and not c.outcome:
                summary = f"The {fmt_short(due, r.timezone)} {what} check-in didn't go through"
                event = HearthEvent(id=new_id("ev"), ts=now, resident_id=r.id, checkin_id=c.id, kind="missed",
                                    source=c.transport, summary=summary, evidence=(error or "")[:240])
            elif final in ("refused", "concern") and not c.outcome:
                summary = "Ended early"
            c = c.model_copy(update={"status": "ended", "ended_at": now, "outcome": final, "summary": summary,
                                     "error": error if final == "error" else None})
            await self._save_checkin(c)
        if event:
            await self.add_event(event, c.kind)
        if alert:
            await self._save_alert(alert)
            await self._notify(alert.title, alert.detail, url=f"/?alert={alert.id}", actions=REFUSAL_ACTIONS[1:])
        if retry:
            self._bg(self._retry_after(c, self.settings.retry_seconds))

    async def _retry_after(self, first: Checkin, seconds: float) -> None:
        await asyncio.sleep(seconds)
        for _ in range(60):
            try:
                nc = await self.start(StartCheckinRequest(kind=first.kind, transport=first.transport,
                                                          cue_id=first.cue_id,
                                                          mock_outcome=self.mock_requested.get(first.id)),
                                      trigger="retry", schedule_item_id=first.schedule_item_id, attempt=2)
                self.retry_origin[nc.id] = self.retry_origin.get(first.id, first.created_at)
                return
            except Conflict:
                await asyncio.sleep(5)
            except Exception:
                log.exception("retry check-in failed to start")
                return

    # ------------------------------------------------------------------ tools (§6)
    async def _open_for_outcome(self, checkin_id: str) -> Checkin | None:
        """The check-in if an outcome may still be set, else None (set-once rule)."""
        c = await self._get(checkin_id)
        if c.outcome is not None or c.status == "ended":
            return None
        return c

    async def _confirm(self, checkin_id: str, kind: str, confidence: float, evidence: str, *,
                       amount: str | None = None, steps: list[str] | None = None) -> ToolResult:
        r = await self.resident()
        async with self.lock:
            c = await self._open_for_outcome(checkin_id)
            if c is None:
                return ToolResult(ok=True)
            now = self.clock.now()
            label = await self._label_for(c)
            evidence = self._redact(evidence) or ""
            if confidence < LOW_CONFIDENCE:
                ev = HearthEvent(id=new_id("ev"), ts=now, resident_id=r.id, checkin_id=c.id, kind="note",
                                 source=c.transport, confidence=confidence, evidence=evidence,
                                 summary=f"Not sure yet about her {wording.what_for(kind, label)}")
                say = {"drink": "I couldn't quite see. Could you show me your cup once more, and take a sip?",
                       "meal": "I couldn't quite see. Could you show me your plate once more?",
                       "med": "I couldn't quite see. Could you show me the pill box once more?"}[kind]
                save = None
            else:
                if kind == "drink":
                    summary = wording.drink_summary(amount or "some")
                    extra = {"amount": amount, "volume_ml_est": AMOUNT_ML.get(amount or "some")}
                elif kind == "meal":
                    summary = wording.meal_summary(amount or "some", wording.meal_name(label, now, r.timezone))
                    extra = {"amount": amount}
                else:
                    summary = wording.med_summary(wording.med_period(label, now, r.timezone))
                    extra = {"med_steps": steps or []}
                ev = HearthEvent(id=new_id("ev"), ts=now, resident_id=r.id, checkin_id=c.id, kind=kind,
                                 source=c.transport, confidence=confidence, evidence=evidence, summary=summary,
                                 **extra)
                say = f"Thank you, {r.preferred_name}."
                save = c.model_copy(update={"outcome": "confirmed", "summary": summary})
                await self._save_checkin(save)
        await self.add_event(ev, c.kind)
        if save is not None:
            title = {"drink": f"✓ {r.family_name_for_her} had some water",
                     "meal": f"✓ {r.family_name_for_her} had her {wording.meal_name(label, now, r.timezone)}",
                     "med": f"✓ {r.family_name_for_her} took her {wording.what_for('med', label)}"}[kind]
            body_bits = {"drink": wording.DRINK_BODY.get(amount or "some", "drank some"),
                         "meal": wording.MEAL_AMOUNT.get(amount or "some", "ate some of it"),
                         "med": "observed taking"}[kind]
            await self._notify(title, f"{fmt_time(now, r.timezone)} · {body_bits}", url="/")
        return ToolResult(ok=True, event_id=ev.id, say=say)

    async def confirm_drink(self, checkin_id: str, body: ToolConfirmDrink) -> ToolResult:
        return await self._confirm(checkin_id, "drink", body.confidence, body.evidence, amount=body.amount)

    async def confirm_meal(self, checkin_id: str, body: ToolConfirmMeal) -> ToolResult:
        return await self._confirm(checkin_id, "meal", body.confidence, body.evidence, amount=body.amount)

    async def confirm_med(self, checkin_id: str, body: ToolConfirmMed) -> ToolResult:
        return await self._confirm(checkin_id, "med", body.confidence, body.evidence, steps=list(body.steps_seen))

    async def report_refusal(self, checkin_id: str, body: ToolReportRefusal) -> ToolResult:
        r = await self.resident()
        async with self.lock:
            c = await self._open_for_outcome(checkin_id)
            if c is None:
                return ToolResult(ok=True)
            now = self.clock.now()
            label = await self._label_for(c)
            what = wording.what_for(c.kind, label)
            said = self._redact(body.said) or ""
            due = await self._due(c, r)
            ev = HearthEvent(id=new_id("ev"), ts=now, resident_id=r.id, checkin_id=c.id, kind="refused",
                             source=c.transport, summary=f"Said no to her {what}", said=said)
            asked = "Asked twice, kindly." if body.asked_times >= 2 else "Asked once, kindly."
            alert = Alert(id=new_id("al"), ts=now, resident_id=r.id, checkin_id=c.id, event_id=ev.id,
                          kind="refused", tier="check",
                          title=f"{r.family_name_for_her} didn't want her {fmt_short(due, r.timezone)} {what}",
                          detail=f"She said: {said}. {asked}" if said else asked, said=said or None)
            await self._save_checkin(c.model_copy(update={"outcome": "refused", "summary": ev.summary}))
        await self.add_event(ev, c.kind)
        await self._save_alert(alert)
        await self._notify(alert.title, alert.detail, url=f"/?alert={alert.id}", actions=REFUSAL_ACTIONS)
        return ToolResult(ok=True, event_id=ev.id, say="That's alright. I'll let Sarah know.")

    async def flag_concern(self, checkin_id: str, body: ToolFlagConcern) -> ToolResult:
        """Safety beats set-once: a concern is always recorded (once per check-in) and becomes the outcome."""
        r = await self.resident()
        async with self.lock:
            c = await self._get(checkin_id)
            if c.outcome == "concern":
                return ToolResult(ok=True)
            now = self.clock.now()
            quote = self._redact(body.evidence_quote) or ""
            at = fmt_time(now, r.timezone)
            if body.category in SPOKEN_CONCERN or body.detected_by == "keyword":
                seen = f"she said '{quote}' at {at}"
            else:
                seen = f"{quote.rstrip('.')} at {at}" if quote else f"she wasn't responding at {at}"
            ev = HearthEvent(id=new_id("ev"), ts=now, resident_id=r.id, checkin_id=c.id, kind="concern",
                             source=c.transport, summary="The check-in noticed something unusual",
                             said=quote or None, concern_category=body.category, evidence=quote)
            alert = Alert(id=new_id("al"), ts=now, resident_id=r.id, checkin_id=c.id, event_id=ev.id,
                          kind="concern", tier="urgent", title=f"Please check on {r.family_name_for_her} now",
                          detail=f"The check-in noticed something unusual: {seen}. Please check on her now.",
                          said=quote or None)
            upd: dict[str, Any] = {"outcome": "concern", "summary": "The check-in noticed something unusual"}
            await self._save_checkin(c.model_copy(update=upd))
        await self.add_event(ev, c.kind)
        await self._save_alert(alert)
        await self._notify(alert.title, alert.detail, urgent=True, url=f"/?alert={alert.id}", actions=CONCERN_ACTIONS)
        return ToolResult(ok=True, event_id=ev.id,
                          say=f"I'm going to get {r.daughter_name} for you right now. Stay where you are.")

    async def end_checkin(self, checkin_id: str, body: ToolEndCheckin) -> ToolResult:
        c = await self._get(checkin_id)
        if c.status == "ended":
            return ToolResult(ok=True)
        if c.outcome is None and body.reason == "distressed":
            await self.report_refusal(checkin_id, ToolReportRefusal(said="seemed upset, so the call ended kindly",
                                                                    asked_times=1))
        self.ending[checkin_id] = (END_REASON_OUTCOME.get(body.reason), body.detail or None, False)
        service = self.services.get(checkin_id)
        if service is not None:
            try:
                await service.end(checkin_id, body.reason)
            except Exception as e:  # noqa: BLE001
                log.warning("call end failed: %s", e)
        await self._finish(checkin_id, END_REASON_OUTCOME.get(body.reason), body.detail or None)
        return ToolResult(ok=True)

    async def cancel(self, checkin_id: str) -> Checkin:
        c = await self._get(checkin_id)
        if c.status != "ended":
            self.ending[checkin_id] = (None, None, True)
            service = self.services.get(checkin_id)
            if service is not None:
                try:
                    await service.end(checkin_id, "cancelled")
                except Exception as e:  # noqa: BLE001
                    log.warning("call end failed: %s", e)
            await self._finish(checkin_id, None, None, cancelled=True)
            self.ending.pop(checkin_id, None)
        return await self._get(checkin_id)

    # ------------------------------------------------------------------ alerts
    async def alert_action(self, alert_id: str, req: AlertActionRequest) -> Alert:
        a = await self.store.get_alert(alert_id)
        if a is None:
            raise NotFound("That alert doesn't exist")
        now = self.clock.now()
        upd: dict[str, Any] = {"status": "handled", "action": req.action, "action_at": now}
        if req.action == "retry_later":
            upd["retry_at"] = now + timedelta(minutes=req.retry_minutes or 30)
        return await self._save_alert(a.model_copy(update=upd))

    async def _due_retries(self, now: datetime) -> None:
        fired = set(await self.store.kv_get("retry_fired") or [])
        for a in await self.store.list_alerts():
            if a.action != "retry_later" or a.retry_at is None or a.retry_at > now or a.id in fired:
                continue
            if await self.store.active_checkin() is not None:
                return
            kind, item_id, cue_id = "drink", None, None
            if a.checkin_id:
                old = await self.store.get_checkin(a.checkin_id)
                if old:
                    kind, item_id, cue_id = old.kind, old.schedule_item_id, old.cue_id
            fired.add(a.id)
            await self.store.kv_set("retry_fired", sorted(fired))
            try:
                await self.start(StartCheckinRequest(kind=kind, cue_id=cue_id), trigger="retry",
                                 schedule_item_id=item_id)
            except Conflict:
                return

    # ------------------------------------------------------------------ schedule tick
    async def tick(self) -> None:
        now = self.clock.now()
        last, self._last_tick = self._last_tick, now
        try:
            r = await self.resident()
        except NotFound:
            return
        await self._due_retries(now)
        if last is None or now <= last:
            return
        local_today = now.astimezone(ZoneInfo(r.timezone)).date()
        key = f"ran:{local_today.isoformat()}"
        ran = set(await self.store.kv_get(key) or [])
        crossed = [i for i in await self.store.list_schedule()
                   if i.enabled and i.id not in ran and not in_quiet_hours(i.time, r.quiet_hours)
                   and last < at_local(local_today, i.time, r.timezone) <= now]
        if not crossed:
            return
        await self.store.kv_set(key, sorted(ran | {i.id for i in crossed}))
        # A big jump of the demo clock marks everything it skipped as done; only an item that
        # fell due in the last few minutes actually rings.
        fresh = [i for i in crossed if at_local(local_today, i.time, r.timezone) >= now - SCHEDULE_GRACE]
        if not fresh:
            return
        item = max(fresh, key=lambda i: i.time)
        if await self.store.active_checkin() is not None:
            log.info("schedule: %s is due but a check-in is running; skipped", item.id)
            return
        try:
            await self.start(StartCheckinRequest(kind=item.kind, cue_id=item.cue_id), trigger="schedule",
                             schedule_item_id=item.id)
            log.info("schedule: started %s", item.id)
        except Conflict:
            pass

    async def run_scheduler(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("schedule tick failed")
            await asyncio.sleep(self.settings.tick_seconds)

    def reset_tick(self) -> None:
        self._last_tick = self.clock.now()

    # ------------------------------------------------------------------ views
    async def next_item(self, r: Resident) -> tuple[ScheduleItem | None, datetime | None]:
        now = self.clock.now()
        today = now.astimezone(ZoneInfo(r.timezone)).date()
        items = [i for i in await self.store.list_schedule()
                 if i.enabled and not in_quiet_hours(i.time, r.quiet_hours)]
        for d in (today, today + timedelta(days=1)):
            for i in items:
                at = at_local(d, i.time, r.timezone)
                if at > now:
                    return i, at
        return None, None

    async def today(self) -> Today:
        r = await self.resident()
        now = self.clock.now()
        start, end = local_day_bounds(now.astimezone(ZoneInfo(r.timezone)).date(), r.timezone)
        evs = await self.store.list_events(since=start, until=end, limit=500)
        items = await self.store.list_schedule()
        nxt, nxt_at = await self.next_item(r)
        return Today(
            now=now, resident=r,
            fluid_ml=sum(e.volume_ml_est or 0 for e in evs if e.kind == "drink"),
            fluid_target_ml=r.fluid_target_ml,
            drinks_confirmed=sum(1 for e in evs if e.kind == "drink"),
            meals_confirmed=sum(1 for e in evs if e.kind == "meal"),
            meds_confirmed=sum(1 for e in evs if e.kind == "med"),
            meds_expected=sum(1 for i in items if i.enabled and i.kind == "med"),
            next_checkin=nxt, next_checkin_at=nxt_at,
            active_checkin=await self.store.active_checkin(),
            open_alerts=await self.store.list_alerts("open"),
            events=evs,
        )

    async def _python_day_stats(self, days: list[date], tz: str) -> list[DayStats]:
        start, _ = local_day_bounds(days[0], tz)
        _, end = local_day_bounds(days[-1], tz)
        if isinstance(self.store, MemoryStore):
            pairs = [(e, k) for e, k in self.store.events_with_kind() if start <= e.ts < end]
        else:
            pairs = [(e, None) for e in await self.store.list_events(since=start, until=end, limit=100000)]
        z = ZoneInfo(tz)
        out = []
        for d in days:
            dev = [(e, k) for e, k in pairs if e.ts.astimezone(z).date() == d]
            out.append(DayStats(
                day=d.isoformat(),
                fluid_ml=sum(e.volume_ml_est or 0 for e, _ in dev if e.kind == "drink"),
                drinks_confirmed=sum(1 for e, _ in dev if e.kind == "drink"),
                drinks_refused=sum(1 for e, k in dev if e.kind == "refused" and (k or "drink") == "drink"),
                meals_confirmed=sum(1 for e, _ in dev if e.kind == "meal"),
                meds_confirmed=sum(1 for e, _ in dev if e.kind == "med"),
                meds_expected=0,
                checkins_missed=sum(1 for e, _ in dev if e.kind == "missed"),
            ))
        return out

    async def week(self) -> Week:
        r = await self.resident()
        now = self.clock.now()
        local = now.astimezone(ZoneInfo(r.timezone))
        today = local.date()
        days = [today - timedelta(days=n) for n in range(6, -1, -1)]
        stats = await self.store.day_stats(days, r.timezone)
        source = "tiger_continuous_aggregate"
        if stats is None:
            stats = await self._python_day_stats(days, r.timezone)
            source = "memory"
        meds = [i for i in await self.store.list_schedule() if i.enabled and i.kind == "med"]
        hhmm = local.strftime("%H:%M")
        out = []
        for d, s in zip(days, stats):
            active = (s.fluid_ml or s.drinks_confirmed or s.drinks_refused or s.meals_confirmed
                      or s.meds_confirmed or s.checkins_missed)
            if d == today:
                expected = sum(1 for i in meds if i.time <= hhmm)
            else:
                expected = len(meds) if active else 0  # no history before the service started
            out.append(s.model_copy(update={"meds_expected": expected}))
        streak = 0
        for s in reversed(out):
            if s.day == today.isoformat() and s.meds_confirmed < s.meds_expected:
                continue  # today isn't over yet
            if s.meds_expected and s.meds_confirmed >= s.meds_expected:
                streak += 1
            else:
                break
        return Week(days=out, fluid_target_ml=r.fluid_target_ml, med_streak_days=streak, source=source)

    async def add_manual_event(self, req: ManualEventRequest) -> HearthEvent:
        r = await self.resident()
        ev = HearthEvent(id=new_id("ev"), ts=req.ts or self.clock.now(), resident_id=r.id, kind=req.kind,
                         source="manual", summary=req.summary, amount=req.amount,
                         volume_ml_est=AMOUNT_ML.get(req.amount) if req.kind == "drink" and req.amount else None)
        return await self.add_event(ev, req.kind if req.kind != "note" else None)
