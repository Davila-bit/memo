"""Fakes for the LiveKit room API and the Chert Calls API. No network, no keys."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from hearth_contracts.models import AgentJob, Checkin, Consent, Resident

from hearth.calls.chert import ChertResult
from hearth.calls.config import CallSettings, Timings
from hearth.calls.livekit_ops import ParticipantState, TrackState

FAST = Timings(
    agent_join=0.3, ring=0.4, poll=0.01, live_poll=0.01, kiosk_presence=0.2, kiosk_answer=0.3,
    ending_grace=0.1, end_wait=0.15, http=1.0, cleanup=3.0, watchdog_extra=2.0,
)

SETTINGS = CallSettings(
    livekit_url="wss://test.livekit.cloud", livekit_api_key="APIkey123", livekit_api_secret="s" * 40,
    chert_api_key="ck", chert_project_id="proj", chert_line_id="line-1", chert_ipad_number="4045550123",
)


def resident() -> Resident:
    return Resident(
        id="margaret", name="Margaret Ellis", preferred_name="Margaret", family_name_for_her="Mum",
        daughter_name="Sarah", consent=Consent(status="signed", signed_by="Margaret Ellis", relation="self"),
    )


def checkin(transport: str = "facetime", cid: str = "ck1", room: str | None = None) -> Checkin:
    return Checkin(
        id=cid, resident_id="margaret", kind="drink", transport=transport, status="queued",  # type: ignore[arg-type]
        room=room or f"checkin-{cid}", created_at=datetime.now(UTC),
    )


def job(c: Checkin, max_seconds: int = 5) -> AgentJob:
    return AgentJob(
        checkin_id=c.id, kind=c.kind, transport=c.transport, resident=resident(), api_base="http://x",
        caller_identity_prefix="chert-bridge-" if c.transport == "facetime" else "kiosk-",
        max_seconds=max_seconds,
    )


def agent_participant(identity: str = "agent-AJ_1") -> ParticipantState:
    return ParticipantState(identity, is_agent=True,
                            tracks=[TrackState("audio", muted=False), TrackState("video", muted=False)])


class FakeRooms:
    def __init__(self, *, agent_joins: bool = True, kiosk: bool = False, kiosk_answers: bool = True,
                 device: str = "kitchen") -> None:
        self.agent_joins = agent_joins
        self.kiosk_answers = kiosk_answers
        self.rooms: dict[str, list[ParticipantState]] = {}
        self.metadata: dict[str, list[str]] = {}
        self.log: list[tuple[str, ...]] = []
        self.kiosk_id = f"kiosk-{device}"
        if kiosk:
            self.rooms[self.kiosk_id] = [ParticipantState(
                self.kiosk_id, tracks=[TrackState("audio", muted=True), TrackState("video", muted=True)])]

    async def ensure_room(self, room: str, *, empty_timeout: int, max_participants: int) -> None:
        self.log.append(("ensure_room", room, str(empty_timeout), str(max_participants)))
        self.rooms.setdefault(room, [])

    async def delete_room(self, room: str) -> None:
        self.log.append(("delete_room", room))
        self.rooms.pop(room, None)

    async def dispatch_agent(self, room: str, agent_name: str, metadata: str) -> str:
        self.log.append(("dispatch", room, agent_name))
        self.last_metadata = metadata
        if self.agent_joins:
            self.rooms.setdefault(room, []).append(agent_participant())
        return "AD_1"

    async def delete_dispatch(self, dispatch_id: str, room: str) -> None:
        self.log.append(("delete_dispatch", dispatch_id, room))

    async def participants(self, room: str) -> list[ParticipantState]:
        parts = self.rooms.get(room, [])
        if self.kiosk_answers and any(p.is_agent for p in parts):
            for p in parts:
                if p.identity == self.kiosk_id:
                    for t in p.tracks:
                        t.muted = False
        return list(parts)

    async def set_metadata(self, room: str, metadata: str) -> None:
        self.metadata.setdefault(room, []).append(metadata)

    async def remove_participant(self, room: str, identity: str) -> None:
        self.log.append(("remove", room, identity))
        self.rooms[room] = [p for p in self.rooms.get(room, []) if p.identity != identity]

    def join_bridge(self, room: str, identity: str) -> None:
        self.rooms.setdefault(room, []).append(
            ParticipantState(identity, tracks=[TrackState("audio"), TrackState("video")]))

    def agent_leaves(self, room: str) -> None:
        self.rooms[room] = [p for p in self.rooms.get(room, []) if not p.is_agent]

    def names(self) -> list[str]:
        return [e[0] for e in self.log]

    async def aclose(self) -> None:
        pass


def ev(t: str, source: str = "worker") -> dict[str, Any]:
    return {"event_type": t, "source": source, "payload": {}}


class FakeChert:
    """`script` is the list of GET /calls/{id} bodies, one per poll; the last one repeats.
    After POST /end the call reads `after_end` (default: ended) unless `stuck_after_end`."""

    def __init__(self, script: list[dict[str, Any]] | None = None, *, create_status: int = 201,
                 create_body: dict[str, Any] | None = None, lines: list[dict[str, Any]] | None = None,
                 stuck_after_end: str | None = None, rooms: FakeRooms | None = None) -> None:
        self.script = script or [{"status": "allocated"}]
        self.create_status = create_status
        self.create_body = create_body
        self.lines = lines if lines is not None else [{"id": "line-1", "status": "available",
                                                       "availability": "available", "current_call_id": None}]
        self.stuck_after_end = stuck_after_end
        self.rooms = rooms
        self.created: list[tuple[dict[str, Any], str]] = []
        self.ended: list[str] = []
        self.polls = 0
        self._i = 0

    async def list_lines(self) -> list[dict[str, Any]]:
        return self.lines

    async def create_call(self, body: dict[str, Any], idempotency_key: str) -> ChertResult:
        self.created.append((body, idempotency_key))
        if self.create_status != 201:
            return ChertResult(self.create_status, self.create_body or {"error": {"code": "bad"}})
        return ChertResult(201, {"data": {"id": "call-1", "status": "allocated"}, "request_id": "r"})

    async def get_call(self, call_id: str) -> dict[str, Any]:
        self.polls += 1
        if self.ended:
            if self.stuck_after_end:
                return {"id": call_id, "status": self.stuck_after_end}
            return {"id": call_id, "status": "ended", "terminal_reason": "unknown",
                    "terminal_detail": "api_requested"}
        body = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        if self.rooms is not None and body.get("status") in ("dialing", "active"):
            room = next((r for r in self.rooms.rooms if r.startswith("checkin-")), None)
            if room and not any(p.identity.startswith("chert-bridge-") for p in self.rooms.rooms[room]):
                self.rooms.join_bridge(room, "chert-bridge-" + room.removeprefix("checkin-"))
        return {"id": call_id, **body}

    async def get_events(self, call_id: str) -> list[dict[str, Any]]:
        return []

    async def end_call(self, call_id: str, idempotency_key: str) -> int:
        self.ended.append(idempotency_key)
        return 202

    async def aclose(self) -> None:
        pass


class RecordingCallbacks:
    def __init__(self, on_connected=None) -> None:
        self.events: list[tuple[str, Any, Any]] = []
        self.done = asyncio.Event()
        self.on_connected = on_connected

    async def call_ringing(self, checkin_id: str) -> None:
        self.events.append(("ringing", None, None))

    async def call_connected(self, checkin_id: str) -> None:
        self.events.append(("connected", None, None))
        if self.on_connected:
            await self.on_connected()

    async def call_finished(self, checkin_id: str, outcome: str | None, error: str | None) -> None:
        self.events.append(("finished", outcome, error))
        self.done.set()

    def kinds(self) -> list[str]:
        return [e[0] for e in self.events]

    def finished(self) -> list[tuple[str, Any, Any]]:
        return [e for e in self.events if e[0] == "finished"]
