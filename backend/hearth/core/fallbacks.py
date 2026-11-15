"""Built-in fallbacks so the core runs alone: mock calls, log-only notifier, keyword search,
template digest, regex redactor. The real modules (hearth.calls, hearth.integrations) replace
them when present."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any

from hearth_contracts import (
    AgentCaption,
    AgentJob,
    AgentStatus,
    Alert,
    AskHit,
    AskResponse,
    Checkin,
    Digest,
    HearthEvent,
    KioskToken,
    ToolConfirmDrink,
    ToolConfirmMeal,
    ToolConfirmMed,
    ToolEndCheckin,
    ToolFlagConcern,
    ToolReportRefusal,
)

log = logging.getLogger("hearth.core")


# ---------------------------------------------------------------------------- calls


class MockCallService:
    """Scripted call for transport `mock`. Drives the same callbacks and the same tool functions
    the real agent would hit over /internal, so the whole lifecycle is exercised without keys."""

    def __init__(self, scale: float = 1.0, max_seconds: int = 90) -> None:
        self.scale = scale
        self.max_seconds = max_seconds
        self.tasks: dict[str, asyncio.Task] = {}
        self.finished: set[str] = set()
        self.cbs: dict[str, Any] = {}
        self.outcomes: dict[str, str] = {}  # checkin_id -> scripted ending (StartCheckinRequest.mock_outcome)

    def health(self) -> dict[str, str]:
        return {"mock_calls": "mock"}

    async def kiosk_token(self, device: str) -> KioskToken:
        raise RuntimeError("Kiosk calls need the LiveKit call module")

    async def start(self, checkin: Checkin, job: AgentJob, cb: Any) -> None:
        self.cbs[checkin.id] = cb
        self.tasks[checkin.id] = asyncio.create_task(self._run(checkin, job, cb), name=f"mock-{checkin.id}")

    async def _finish(self, checkin_id: str, outcome: str | None, error: str | None) -> None:
        if checkin_id in self.finished:
            return
        self.finished.add(checkin_id)
        cb = self.cbs.pop(checkin_id, None)
        self.tasks.pop(checkin_id, None)
        if cb is not None:
            await cb.call_finished(checkin_id, outcome, error)

    async def end(self, checkin_id: str, reason: str) -> None:
        task = self.tasks.get(checkin_id)
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        await self._finish(checkin_id, None, None)

    async def _sleep_until(self, t0: float, at: float) -> None:
        loop = asyncio.get_running_loop()
        delay = t0 + at * self.scale - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)

    async def _run(self, checkin: Checkin, job: AgentJob, cb: Any) -> None:
        try:
            await asyncio.wait_for(self._script(checkin, job, cb), timeout=self.max_seconds * self.scale + 1)
        except TimeoutError:
            await self._finish(checkin.id, "error", "The call reached its 90-second limit")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("mock call crashed")
            await self._finish(checkin.id, "error", f"Mock call crashed: {type(e).__name__}")

    async def _script(self, checkin: Checkin, job: AgentJob, cb: Any) -> None:
        cid = checkin.id
        ending = self.outcomes.pop(cid, None) or "confirmed"
        name = job.resident.preferred_name
        what = {"drink": "some water", "meal": "your lunch", "med": "your tablets"}[checkin.kind]
        cue = job.cue.title.lower() if job.cue else {"drink": "your cup", "meal": "your plate",
                                                      "med": "your pill box"}[checkin.kind]
        t0 = asyncio.get_running_loop().time()

        await self._sleep_until(t0, 1)
        await cb.call_ringing(cid)
        if ending == "no_answer":
            await self._sleep_until(t0, 9)
            await self._finish(cid, "no_answer", None)
            return
        if ending == "error":
            await self._sleep_until(t0, 4)
            await self._finish(cid, "error", "The mock call failed on purpose")
            return

        await self._sleep_until(t0, 3)
        await cb.call_connected(cid)
        await self._sleep_until(t0, 5)
        await cb.agent_status(cid, AgentStatus(status="watching"))
        await cb.caption(cid, AgentCaption(speaker="helper",
                                           text=f"Hello {name}, it's Sarah's helper. I'm an assistant."))
        await self._sleep_until(t0, 6)
        await cb.caption(cid, AgentCaption(speaker="helper", text=f"Time for {what}. Could you show me {cue}?"))
        await self._sleep_until(t0, 7.5)

        if ending == "refused":
            await cb.caption(cid, AgentCaption(speaker="her", text="Not now, love. I'm not thirsty, I had tea."))
            await self._sleep_until(t0, 9)
            await cb.report_refusal(cid, ToolReportRefusal(said="not thirsty, had tea", asked_times=2))
            await cb.end_checkin(cid, ToolEndCheckin(reason="refused"))
        elif ending == "concern":
            await cb.caption(cid, AgentCaption(speaker="her", text="I fell down earlier. I can't get up."))
            await self._sleep_until(t0, 9)
            await cb.flag_concern(cid, ToolFlagConcern(category="said_fell_or_hurt",
                                                       evidence_quote="I fell down earlier",
                                                       detected_by="keyword"))
            await cb.caption(cid, AgentCaption(speaker="helper",
                                               text="I'm going to get Sarah for you right now. Stay where you are."))
            await cb.end_checkin(cid, ToolEndCheckin(reason="concern"))
        else:
            await cb.caption(cid, AgentCaption(speaker="her", text="Here it is."))
            await self._sleep_until(t0, 9)
            if checkin.kind == "drink":
                await cb.confirm_drink(cid, ToolConfirmDrink(
                    amount="most", confidence=0.9,
                    evidence="Held the mug up, drank twice, mug lower after"))
            elif checkin.kind == "meal":
                await cb.confirm_meal(cid, ToolConfirmMeal(
                    amount="most", confidence=0.85, evidence="Showed the plate, ate several forkfuls"))
            else:
                await cb.confirm_med(cid, ToolConfirmMed(
                    steps_seen=["slot_opened", "pill_in_hand", "hand_to_mouth", "drank_water", "slot_shown_empty"],
                    confidence=0.85, evidence="Opened the slot, tablets to mouth, sip of water, slot shown empty"))
            await cb.caption(cid, AgentCaption(speaker="helper", text=f"Thank you, {name}. Have a lovely afternoon."))
            await cb.end_checkin(cid, ToolEndCheckin(reason="done"))


# ---------------------------------------------------------------------------- notifier


class LogNotifier:
    async def notify(self, title: str, body: str, *, urgent: bool = False, url: str | None = None,
                     actions: list[tuple[str, str]] | None = None) -> None:
        log.info("notify%s: %s | %s", " (urgent)" if urgent else "", title, body)

    async def alert(self, alert: Alert) -> None:
        await self.notify(alert.title, alert.detail, urgent=alert.tier == "urgent")

    def health(self) -> str:
        return "mock"


# ---------------------------------------------------------------------------- search

_WORD = re.compile(r"[a-z0-9']+")
_STOP = {"did", "the", "a", "an", "her", "she", "mum", "mom", "is", "has", "have", "how", "much", "what",
         "when", "was", "to", "of", "on", "in", "any", "does", "do", "today", "yesterday", "take", "i"}
_SYN = {"pills": "med", "pill": "med", "tablets": "med", "tablet": "med", "medication": "med", "meds": "med",
        "medicine": "med", "water": "drink", "drank": "drink", "drunk": "drink", "drinks": "drink",
        "tea": "drink", "lunch": "meal", "dinner": "meal", "breakfast": "meal", "eat": "meal", "ate": "meal",
        "refuse": "refused", "refusal": "refused", "declined": "refused", "missed": "missed"}


def _terms(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP:
            continue
        out.append(_SYN.get(w, w))
    return out


class KeywordSearch:
    async def index(self, event: HearthEvent) -> None:
        return None

    async def clear(self) -> None:
        return None

    async def ask(self, q: str, now: datetime, recent: list[HearthEvent]) -> AskResponse:
        terms = set(_terms(q))
        scored: list[tuple[int, HearthEvent]] = []
        for e in recent:
            words = Counter(_terms(" ".join([e.kind, e.summary, e.evidence or "", e.said or ""])))
            score = sum(words[t] for t in terms)
            if score:
                scored.append((score, e))
        scored.sort(key=lambda x: (x[0], x[1].ts), reverse=True)
        hits = [AskHit(event=e, snippet=e.summary) for _, e in scored[:5]]
        if not hits:
            answer = "I couldn't find that in the last week of check-ins."
        else:
            top = hits[0].event
            answer = f"Closest match: {top.summary} ({top.ts.strftime('%a %d %b, %H:%M')} UTC)."
        return AskResponse(q=q, answer=answer, hits=hits, engine="fallback")

    def health(self) -> str:
        return "mock"


# ---------------------------------------------------------------------------- digest


class TemplateDigest:
    async def write(self, day: str, events: list[HearthEvent], her_name: str) -> Digest:
        drinks = [e for e in events if e.kind == "drink"]
        ml = sum(e.volume_ml_est or 0 for e in drinks)
        meds = sum(1 for e in events if e.kind == "med")
        meals = sum(1 for e in events if e.kind == "meal")
        refused = sum(1 for e in events if e.kind == "refused")
        missed = sum(1 for e in events if e.kind == "missed")
        s1 = (f"{her_name} had {len(drinks)} drink{'s' if len(drinks) != 1 else ''} (about {ml} ml)."
              if drinks else f"No drinks were seen for {her_name}.")
        s2 = f"She took her tablets {meds} time{'s' if meds != 1 else ''} and had {meals} meal{'s' if meals != 1 else ''}."
        if refused or missed:
            parts = []
            if refused:
                parts.append(f"said no {refused} time{'s' if refused != 1 else ''}")
            if missed:
                parts.append(f"missed {missed} check-in{'s' if missed != 1 else ''}")
            s3 = "She " + " and ".join(parts) + "."
        else:
            s3 = "Every check-in went smoothly."
        return Digest(day=day, text=f"{s1} {s2} {s3}", generated_by="template")

    def health(self) -> str:
        return "mock"


# ---------------------------------------------------------------------------- redactor

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{7,}\d)(?!\w)")
_ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Court|Ct|Way|"
    r"Boulevard|Blvd|Place|Pl|Terrace|Circle|Cir)\b\.?", re.IGNORECASE)


class RegexRedactor:
    def redact(self, text: str) -> str:
        if not text:
            return text
        text = _EMAIL.sub("[email]", text)
        text = _ADDRESS.sub("[address]", text)
        text = _PHONE.sub("[phone]", text)
        return text

    def health(self) -> str:
        return "mock"
