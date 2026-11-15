"""Opt-in, process-local watch state. Images never leave the request/classifier stack."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from hearth_contracts import Alert, HearthEvent, Watch, WatchNote

from hearth.integrations.gemini import classify_watch_frame

from .checkins import CheckinService, new_id


class WatchService:
    def __init__(self, checkins: CheckinService) -> None:
        self.svc = checkins
        self.until: datetime | None = None
        self.notes: list[WatchNote] = []
        self.concerns = 0
        self.alerted = False
        self.last_seen: datetime | None = None
        self.out_of_view_noted = False
        self.generation = 0
        self.frame_lock = asyncio.Lock()
        self.classify = classify_watch_frame

    def view(self) -> Watch:
        if not self.svc.settings.watch:
            return Watch(enabled=False, active=False)
        if self.until is not None and self.svc.clock.now() >= self.until:
            self.stop()
        return Watch(enabled=True, active=self.until is not None, until=self.until, notes=self.notes)

    def start(self, minutes: int) -> Watch:
        self.generation += 1
        now = self.svc.clock.now()
        self.until = now + timedelta(minutes=minutes)
        self.last_seen = now
        self.notes = []
        self.concerns = 0
        self.alerted = False
        self.out_of_view_noted = False
        return self.view()

    def stop(self) -> Watch:
        self.generation += 1
        self.until = None
        self.concerns = 0
        return self.view()

    def add_note(self, status: Literal["ok", "unusual", "concern"], note: str) -> None:
        # Reuse the evidence redactor; never retain unredacted model text.
        note = " ".join((self.svc._redact(note) or "").split()[:10])[:200]
        if not note or (self.notes and self.notes[-1].note == note):
            return
        self.notes.append(WatchNote(ts=self.svc.clock.now(), status=status, note=note))
        self.notes = self.notes[-30:]

    async def require_active(self) -> None:
        if not self.svc.settings.watch:
            raise HTTPException(404, "Watch mode is not enabled.")
        if not self.view().active or await self.svc.store.active_checkin() is not None:
            raise HTTPException(409, "Watch mode is paused or off.")

    async def frame(self, jpeg: bytes) -> Watch:
        if self.frame_lock.locked():
            raise HTTPException(409, "A watch image is already being checked.")
        async with self.frame_lock:
            async with self.svc.lock:
                await self.require_active()
                generation = self.generation
            result = await self.classify(jpeg)
            async with self.svc.lock:
                # Stop/restart, expiry and a new check-in invalidate an in-flight result.
                await self.require_active()
                if generation != self.generation:
                    raise HTTPException(409, "Watch mode changed while checking.")
                if result is None:
                    self.concerns = 0
                    return self.view()
                now = self.svc.clock.now()
                visible = result.person_visible
                if visible:
                    self.last_seen = now
                self.concerns = self.concerns + 1 if result.status == "concern" else 0
                self.add_note(result.status, result.note)
                if (not visible and self.last_seen is not None
                        and now - self.last_seen > timedelta(minutes=5) and not self.out_of_view_noted):
                    self.add_note("unusual", "Out of view for a while")
                    self.out_of_view_noted = True
                if self.concerns >= 2 and not self.alerted:
                    self.alerted = True
                    r = await self.svc.resident()
                    note = self.svc._redact(result.note) or "[removed]"
                    note = " ".join(note.split()[:10])[:200]
                    event = HearthEvent(
                        id=new_id("ev"), ts=now, resident_id=r.id, kind="concern", source="kiosk",
                        summary=f"{r.family_name_for_her} may have fallen", evidence=note,
                        concern_category="on_floor_or_out_of_view")
                    alert = Alert(id=new_id("al"), ts=now, resident_id=r.id, event_id=event.id,
                                  kind="concern", tier="urgent", title=event.summary, detail=note)
                    await self.svc.raise_concern(event, alert)
                return self.view()
