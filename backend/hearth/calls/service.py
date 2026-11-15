"""CallService: one check-in call on FaceTime (Chert BYOR) or the WebRTC kiosk, with a hard budget.

Whatever happens, every started call ends, its LiveKit/Chert resources are released, and
`cb.call_finished` runs exactly once. No wait in here is unbounded (see `config.Timings`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from hearth_contracts.models import AgentJob, Checkin, CheckinOutcome, KioskToken
from hearth_contracts.ports import CallCallbacks

from .chert import (
    TERMINAL,
    ChertApi,
    ChertResult,
    events_of,
    has_bridge_ready,
    looks_unanswered,
    terminal_text,
)
from .config import CallSettings, Timings, mask_number, to_e164
from .livekit_ops import ParticipantState, RoomOps, mint_token

log = logging.getLogger("hearth.calls")

BRIDGE_PREFIX = "chert-bridge-"
BRIDGE_TTL = 15 * 60  # > 90 s left at allocation, <= 7200 s lifetime (Chert token rules)
KIOSK_TTL = 12 * 3600
CHECKIN_ROOM_EMPTY_TIMEOUT = 30  # seconds LiveKit keeps an empty check-in room
CHECKIN_ROOM_MAX_PARTICIPANTS = 3  # agent + Chert bridge + one spare
INBOUND_PREP_SECONDS = 3.5  # leaves time for core work + HTTP reply before Chert's 5 s deadline
INBOUND_IDENTITY_SECONDS = 2.5
_DEVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

Result = tuple[CheckinOutcome | None, str | None]
T = TypeVar("T")


class CallsUnavailable(RuntimeError):
    """A plain-language reason the call layer cannot do this right now (missing keys etc.)."""


@dataclass
class _Call:
    checkin: Checkin
    job: AgentJob
    cb: CallCallbacks
    transport: Literal["facetime", "kiosk"]
    room: str
    key_base: str
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    end_reason: str | None = None
    finished: bool = False
    task: asyncio.Task[None] | None = None
    room_created: bool = False
    dispatch_id: str | None = None
    agent_identity: str | None = None
    bridge_identity: str | None = None
    chert_call_id: str | None = None
    chert_terminal: bool = False
    ringing_sent: bool = False
    live: bool = False
    metadata_set: bool = False
    inbound: bool = False

    @property
    def id(self) -> str:
        return self.checkin.id


def _is_agent(p: ParticipantState) -> bool:
    return p.is_agent or p.identity.startswith("agent-")


def _agent_ready(p: ParticipantState) -> bool:
    return len(p.audio(unmuted_only=True)) == 1 and len(p.video()) >= 1


class HearthCallService:
    """Implements `hearth_contracts.ports.CallService` for transports `facetime` and `kiosk`."""

    def __init__(
        self,
        settings: CallSettings,
        *,
        rooms: RoomOps | None,
        chert: ChertApi | None,
        timings: Timings | None = None,
    ) -> None:
        self._s = settings
        self._rooms = rooms
        self._chert = chert
        self._t = timings or Timings()
        self._calls: dict[str, _Call] = {}

    # ------------------------------------------------------------------ port

    async def start(self, checkin: Checkin, job: AgentJob, cb: CallCallbacks) -> None:
        if checkin.id in self._calls:
            log.warning("call %s is already running; ignoring a second start", checkin.id)
            return
        transport = self._resolve_transport(checkin)
        room = checkin.room
        if transport == "kiosk":
            kiosk_room = f"kiosk-{self._s.kiosk_device}"
            if room != kiosk_room:
                log.info("kiosk check-in %s: using room %s instead of %s", checkin.id, kiosk_room, room)
            room = kiosk_room
        call = _Call(
            checkin=checkin,
            job=job,
            cb=cb,
            transport=transport,
            room=room,
            key_base=f"hearth-{checkin.id}-a{checkin.attempt}-{secrets.token_hex(4)}",
        )
        self._calls[checkin.id] = call
        call.task = asyncio.create_task(self._run(call), name=f"hearth-call-{checkin.id}")

    async def accept_inbound(self, checkin: Checkin, job: AgentJob, cb: CallCallbacks,
                             call_id: str) -> dict:
        if not self._s.livekit_ready or self._rooms is None or self._chert is None:
            raise CallsUnavailable("Inbound FaceTime is not set up")
        try:
            url = urlsplit(self._s.livekit_url)
            valid = (url.scheme == "wss" and url.hostname and url.port in (None, 443)
                     and not url.username and not url.password and not url.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise CallsUnavailable("Inbound FaceTime needs a secure LiveKit URL")
        if self._calls:
            raise CallsUnavailable("A call is already running")
        call = _Call(checkin=checkin, job=job, cb=cb, transport="facetime", room=checkin.room,
                     key_base=f"hearth-{checkin.id}-inbound", inbound=True,
                     chert_call_id=call_id, bridge_identity=f"{BRIDGE_PREFIX}{checkin.id}")
        self._calls[call.id] = call
        try:
            async with asyncio.timeout(INBOUND_PREP_SECONDS):
                payload = await self._prepare_inbound(call)
        except (Exception, asyncio.CancelledError) as e:
            # Never delay a decline for Chert/LiveKit cleanup. _complete owns the callback once.
            call.task = asyncio.create_task(self._complete(call, "error", "inbound preparation failed"))
            if isinstance(e, asyncio.CancelledError):
                raise
            raise CallsUnavailable(f"Inbound preparation failed ({type(e).__name__})") from e
        call.task = asyncio.create_task(self._run(call), name=f"hearth-inbound-{checkin.id}")
        return payload

    async def _prepare_inbound(self, call: _Call) -> dict:
        assert self._rooms is not None
        s = self._s
        started = asyncio.get_running_loop().time()

        async def room() -> None:
            # Mark before the request: a timed-out create may still have reached LiveKit.
            call.room_created = True
            await self._rooms.ensure_room(call.room, empty_timeout=CHECKIN_ROOM_EMPTY_TIMEOUT,
                                          max_participants=CHECKIN_ROOM_MAX_PARTICIPANTS)

        async def dispatch() -> None:
            call.dispatch_id = await self._rooms.dispatch_agent(
                call.room, s.agent_name, call.job.model_dump_json())

        # Explicit dispatch can create the room too. Ensure its limits concurrently with dispatch.
        async with asyncio.TaskGroup() as group:
            group.create_task(room())
            group.create_task(dispatch())
        remaining = INBOUND_PREP_SECONDS - (asyncio.get_running_loop().time() - started) - 0.05
        try:
            async with asyncio.timeout(min(INBOUND_IDENTITY_SECONDS, max(0, remaining))):
                while True:
                    parts = await self._rooms.participants(call.room)
                    agent = self._pick_agent(call, parts, require_tracks=False)
                    if agent:
                        call.agent_identity = agent.identity
                        break
                    await asyncio.sleep(self._t.poll)
        except TimeoutError:
            pass
        assert s.livekit_api_key and s.livekit_api_secret
        return {
            "action": "accept", "livekit_url": s.livekit_url,
            "participant_token": mint_token(s.livekit_api_key, s.livekit_api_secret,
                                            identity=call.bridge_identity, room=call.room,
                                            ttl_seconds=BRIDGE_TTL),
            "remote_participant_identity": call.agent_identity or s.agent_name,
        }

    async def end(self, checkin_id: str, reason: str) -> None:
        call = self._calls.get(checkin_id)
        if call is None or call.ended.is_set():
            return
        log.info("call %s: end requested (%s)", checkin_id, reason)
        call.end_reason = reason
        call.ended.set()

    async def kiosk_token(self, device: str) -> KioskToken:
        s = self._s
        if not s.livekit_ready:
            raise CallsUnavailable("The kitchen screen can't connect yet: LiveKit keys are missing.")
        device = (device or s.kiosk_device).strip().lower()
        if not _DEVICE_RE.match(device):
            raise CallsUnavailable("That device name isn't valid. Use letters, numbers and dashes.")
        room = f"kiosk-{device}"
        assert s.livekit_api_key and s.livekit_api_secret and s.livekit_url
        token = mint_token(s.livekit_api_key, s.livekit_api_secret, identity=room, room=room,
                           ttl_seconds=KIOSK_TTL, name="Kitchen screen")
        return KioskToken(url=s.livekit_url, token=token, room=room, identity=room)

    def health(self) -> dict[str, str]:
        return {
            "livekit": "ok" if self._rooms is not None else "missing_key",
            "chert": "ok" if self._chert is not None else "missing_key",
        }

    async def aclose(self) -> None:
        for call in list(self._calls.values()):
            call.ended.set()
        tasks = [c.task for c in self._calls.values() if c.task]
        if tasks:
            await asyncio.wait(tasks, timeout=self._t.cleanup + 5)
        for c in (self._rooms, self._chert):
            if c is not None:
                try:
                    await c.aclose()
                except Exception as e:  # noqa: BLE001
                    log.debug("calls: close failed: %s", type(e).__name__)

    # ------------------------------------------------------------------ run

    def _resolve_transport(self, checkin: Checkin) -> Literal["facetime", "kiosk"]:
        if checkin.transport == "facetime":
            if self._chert is None or not self._s.chert_ipad_number:
                log.warning("call %s: Chert keys or iPad number missing; FaceTime falls back to the kiosk", checkin.id)
                return "kiosk"
            return "facetime"
        if checkin.transport != "kiosk":
            log.warning("call %s: transport %r is not handled here; using the kiosk", checkin.id,
                        checkin.transport)
        return "kiosk"

    async def _run(self, call: _Call) -> None:
        t = self._t
        outcome: CheckinOutcome | None = "error"
        error: str | None = "the call stopped unexpectedly"
        try:
            if self._rooms is None:
                log.warning("call %s: LiveKit keys missing; cannot place a call", call.id)
                outcome, error = "error", "LiveKit is not set up, so the call could not be placed"
            else:
                cap = t.agent_join + t.kiosk_presence + t.kiosk_answer + t.ring + call.job.max_seconds
                cap += t.watchdog_extra
                place = self._place_kiosk
                if call.transport == "facetime":
                    place = self._placed_facetime if call.inbound else self._place_facetime
                outcome, error = await asyncio.wait_for(place(call), cap)
        except TimeoutError:
            log.error("call %s: watchdog fired", call.id)
            outcome, error = (None if call.live else "error"), "the call ran too long and was stopped"
        except asyncio.CancelledError:
            log.warning("call %s: task cancelled", call.id)
            outcome, error = (None if call.live else "error"), "the call was stopped"
        except CallsUnavailable as e:
            log.warning("call %s: %s", call.id, e)
            outcome, error = (None if call.live else "error"), str(e)
        except Exception as e:
            log.exception("call %s failed", call.id)
            outcome, error = (None if call.live else "error"), f"call failed: {type(e).__name__}"
        finally:
            await self._complete(call, outcome, error)

    async def _complete(self, call: _Call, outcome: CheckinOutcome | None, error: str | None) -> None:
        await self._cleanup(call)  # bounded inside; never raises
        await self._finish(call, outcome, error)
        self._calls.pop(call.id, None)

    async def _finish(self, call: _Call, outcome: CheckinOutcome | None, error: str | None) -> None:
        if call.finished:
            return
        call.finished = True
        log.info("call %s finished: outcome=%s error=%s", call.id, outcome, error)
        try:
            await call.cb.call_finished(call.id, outcome, error)
        except Exception:
            log.exception("call %s: call_finished callback raised", call.id)

    # ------------------------------------------------------------------ helpers

    async def _pause(self, call: _Call, seconds: float) -> bool:
        """Sleep, waking early if end() is called. True when the call was ended."""
        if call.ended.is_set():
            return True
        try:
            await asyncio.wait_for(call.ended.wait(), seconds)
            return True
        except TimeoutError:
            return False

    async def _safe(self, what: str, aw: Awaitable[T]) -> T | None:
        try:
            return await asyncio.wait_for(aw, self._t.http)
        except Exception as e:  # noqa: BLE001
            log.warning("%s failed: %s", what, type(e).__name__)
            return None

    async def _must(self, what: str, aw: Awaitable[T]) -> T:
        """Like _safe, but a failure ends the call with a plain reason."""
        try:
            return await asyncio.wait_for(aw, self._t.http)
        except Exception as e:
            raise CallsUnavailable(f"could not {what} ({type(e).__name__})") from e

    async def _participants(self, room: str) -> list[ParticipantState] | None:
        assert self._rooms is not None
        return await self._safe(f"list_participants({room})", self._rooms.participants(room))

    async def _poll_participants(
        self, call: _Call, pick: Callable[[list[ParticipantState]], T | None], timeout: float
    ) -> T | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            parts = await self._participants(call.room)
            if parts is not None:
                got = pick(parts)
                if got is not None:
                    return got
            if loop.time() >= deadline:
                return None
            if await self._pause(call, self._t.poll):
                return None

    def _pick_agent(self, call: _Call, parts: list[ParticipantState], *,
                    require_tracks: bool = True) -> ParticipantState | None:
        prefix = call.job.caller_identity_prefix
        cands = [p for p in parts if _is_agent(p)]
        if not cands:  # agent registered under a custom identity: anyone who is not "her"
            cands = [p for p in parts
                     if not p.identity.startswith(prefix) and not p.identity.startswith(BRIDGE_PREFIX)
                     and not p.identity.startswith("kiosk-")]
        for p in cands:
            if not require_tracks or _agent_ready(p):
                return p
        return None

    async def _wait_agent(self, call: _Call) -> ParticipantState | None:
        agent = await self._poll_participants(call, lambda ps: self._pick_agent(call, ps), self._t.agent_join)
        if agent is not None:
            call.agent_identity = agent.identity
            log.info("call %s: agent %s is in %s with audio + video", call.id, agent.identity, call.room)
        return agent

    async def _agent_present(self, call: _Call) -> bool | None:
        parts = await self._participants(call.room)
        if parts is None:
            return None  # unknown; don't act on a failed read
        return any(p.identity == call.agent_identity for p in parts)

    async def _ringing(self, call: _Call) -> None:
        if call.ringing_sent:
            return
        call.ringing_sent = True
        try:
            await call.cb.call_ringing(call.id)
        except Exception:
            log.exception("call %s: call_ringing callback raised", call.id)

    async def _go_live(self, call: _Call) -> None:
        assert self._rooms is not None
        call.live = True
        meta = json.dumps({"hearth_live": True, "checkin_id": call.id})
        await self._safe("set room metadata", self._rooms.set_metadata(call.room, meta))
        call.metadata_set = True
        log.info("call %s: live", call.id)
        await self._ringing(call)
        try:
            await call.cb.call_connected(call.id)
        except Exception:
            log.exception("call %s: call_connected callback raised", call.id)

    # ------------------------------------------------------------------ kiosk

    async def _place_kiosk(self, call: _Call) -> Result:
        assert self._rooms is not None
        kiosk_id = call.room  # identity == room == kiosk-<device>
        t = self._t

        def find_kiosk(ps: list[ParticipantState]) -> list[ParticipantState] | None:
            return ps if any(p.identity == kiosk_id for p in ps) else None

        parts = await self._poll_participants(call, find_kiosk, t.kiosk_presence)
        if call.ended.is_set():
            return None, None
        if parts is None:
            log.info("call %s: kiosk %s is not online", call.id, kiosk_id)
            return "no_answer", None
        for p in parts:  # a helper left over from an earlier check-in must not take this call
            if _is_agent(p):
                log.info("call %s: removing a leftover agent %s from %s", call.id, p.identity, call.room)
                await self._safe("remove leftover agent", self._rooms.remove_participant(call.room, p.identity))

        call.dispatch_id = await self._must(
            "send the helper", self._rooms.dispatch_agent(call.room, self._s.agent_name, call.job.model_dump_json())
        )
        if await self._wait_agent(call) is None:
            return (None, None) if call.ended.is_set() else ("error", "agent did not join")
        await self._ringing(call)

        def answered(ps: list[ParticipantState]) -> ParticipantState | None:
            return next((p for p in ps if p.identity == kiosk_id and p.audio(unmuted_only=True)), None)

        if await self._poll_participants(call, answered, t.kiosk_answer) is None:
            if call.ended.is_set():
                return None, None
            log.info("call %s: kiosk did not answer within %.0f s", call.id, t.kiosk_answer)
            return "no_answer", None
        await self._go_live(call)
        return await self._live_loop(call, her_identity=kiosk_id)

    # ------------------------------------------------------------------ live (both transports)

    async def _live_loop(self, call: _Call, her_identity: str | None) -> Result:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + call.job.max_seconds
        her_seen = False
        next_chert = 0.0
        while True:
            if call.ended.is_set():
                return None, None
            now = loop.time()
            if now >= deadline:
                log.info("call %s: %d s budget reached", call.id, call.job.max_seconds)
                return None, "time budget reached"
            if call.chert_call_id and now >= next_chert:
                next_chert = now + self._t.poll
                c = await self._safe("get call", self._chert.get_call(call.chert_call_id)) if self._chert else None
                if c:
                    st = c.get("status")
                    if st in TERMINAL or st == "ending":
                        call.chert_terminal = st in TERMINAL
                        log.info("call %s: FaceTime %s", call.id, terminal_text(c))
                        return None, (terminal_text(c) if st == "failed" else None)
            parts = await self._participants(call.room)
            if parts is not None:
                ids = {p.identity for p in parts}
                if call.agent_identity not in ids:
                    log.info("call %s: agent left", call.id)
                    return None, None
                if her_identity:
                    if her_identity in ids:
                        her_seen = True
                    elif her_seen or call.transport == "kiosk":
                        log.info("call %s: %s left", call.id, her_identity)
                        return None, None
            if await self._pause(call, self._t.live_poll):
                return None, None

    # ------------------------------------------------------------------ facetime

    async def _line_problem(self) -> str | None:
        """Plain reason the Chert line can't take a call now, or None. Logs the readiness fields."""
        if self._s.skip_line_check or self._chert is None:
            return None
        lines = await self._safe("Chert GET /lines", self._chert.list_lines())
        if lines is None:
            log.warning("could not read the Chert line; trying the call anyway")
            return None
        line = next((ln for ln in lines if ln.get("id") == self._s.chert_line_id), None)
        if line is None:
            return "the FaceTime line is not assigned to this project"
        sh = line.get("session_health") or {}
        rd = sh.get("readiness") or {}
        log.info(
            "chert line: status=%s availability=%s operational=%s current_call=%s session=%s "
            "signed_in=%s control=%s video=%s audio=%s production_media=%s",
            line.get("status"), line.get("availability"), line.get("operational_status"),
            (line.get("current_call_id") or "-")[:8], sh.get("status"), rd.get("facetime_signed_in"),
            rd.get("control_ready"), rd.get("video_ready"), rd.get("audio_ready"),
            rd.get("production_media_ready"),
        )
        if line.get("current_call_id"):
            return f"the FaceTime line is busy with another call ({str(line['current_call_id'])[:8]})"
        for k in ("status", "availability", "operational_status"):
            v = line.get(k)
            if v and v != "available":
                return f"the FaceTime line is {v}"
        if rd.get("facetime_signed_in") is False or rd.get("control_ready") is False:
            return "the FaceTime line's Mac is not ready"
        if rd.get("video_ready") is False:
            return "the FaceTime line can't do video right now"
        # audio_ready is false on an idle healthy line (checked "at_call_start"); session_health.status
        # reads "offline" without a `chert dev` session. Neither blocks a BYOR call.
        return None

    async def _create_call(self, body: dict[str, Any], key: str) -> ChertResult | None:
        assert self._chert is not None
        last: ChertResult | None = None
        for attempt in range(3):
            try:
                res = await asyncio.wait_for(self._chert.create_call(body, key), self._t.http)
            except (httpx.TransportError, TimeoutError) as e:
                log.warning("Chert create call attempt %d: %s; retrying with the same key", attempt + 1,
                            type(e).__name__)
                await asyncio.sleep(1.0)
                continue
            if res.status >= 500:
                last = res
                log.warning("Chert create call attempt %d: HTTP %d; retrying", attempt + 1, res.status)
                await asyncio.sleep(1.0)
                continue
            return res
        return last

    async def _place_facetime(self, call: _Call) -> Result:
        assert self._rooms is not None and self._chert is not None
        s = self._s
        problem = await self._line_problem()
        if problem:
            log.warning("call %s: not dialling: %s", call.id, problem)
            return "error", problem
        if call.job.caller_identity_prefix != BRIDGE_PREFIX:
            log.warning("call %s: job caller prefix %r is not %r", call.id, call.job.caller_identity_prefix,
                        BRIDGE_PREFIX)

        await self._must("create the call room", self._rooms.ensure_room(
            call.room, empty_timeout=CHECKIN_ROOM_EMPTY_TIMEOUT, max_participants=CHECKIN_ROOM_MAX_PARTICIPANTS))
        call.room_created = True
        call.dispatch_id = await self._must(
            "send the helper", self._rooms.dispatch_agent(call.room, s.agent_name, call.job.model_dump_json())
        )
        agent = await self._wait_agent(call)
        if agent is None:
            return (None, None) if call.ended.is_set() else ("error", "agent did not join")

        if not s.chert_ipad_number:
            return "error", "the kitchen iPad number is not configured"
        assert s.livekit_api_key and s.livekit_api_secret
        call.bridge_identity = f"{BRIDGE_PREFIX}{call.id}"
        token = mint_token(s.livekit_api_key, s.livekit_api_secret, identity=call.bridge_identity,
                           room=call.room, ttl_seconds=BRIDGE_TTL)
        try:
            dest = to_e164(s.chert_ipad_number)
        except ValueError:
            return "error", "the kitchen iPad number is not a phone number"
        body = {
            "direction": "outbound",
            "mode": "customer_managed",
            "line_id": s.chert_line_id,
            "destination": dest,
            "destination_type": "phone",
            "media": {"audio": True, "video": True},
            "livekit_url": s.livekit_url,
            "participant_token": token,
            "remote_participant_identity": agent.identity,
        }
        if call.ended.is_set():
            return None, None
        log.info("call %s: dialling %s from the Chert line", call.id, mask_number(dest))
        res = await self._create_call(body, f"{call.key_base}-create")
        if res is None:
            return "error", "could not reach Chert to place the call"
        if res.status not in (200, 201) or not res.data.get("id"):
            log.warning("call %s: Chert refused: HTTP %d %s", call.id, res.status, res.error_text())
            return "error", f"FaceTime call refused: {res.error_text()} (HTTP {res.status})"
        call.chert_call_id = str(res.data["id"])
        log.info("call %s: Chert call %s allocated", call.id, call.chert_call_id)

        return await self._placed_facetime(call)

    async def _placed_facetime(self, call: _Call) -> Result:
        if call.inbound:
            await self._ringing(call)
        try:
            if call.inbound:
                rung = await asyncio.wait_for(self._ring_facetime(call), self._t.ring)
            else:
                rung = await self._ring_facetime(call)
        except TimeoutError:
            return "error", "inbound call never became ready"
        if rung is not None:
            return rung
        await self._go_live(call)
        return await self._live_loop(call, her_identity=call.bridge_identity)

    async def _ring_facetime(self, call: _Call) -> Result | None:
        """None once live; otherwise the result to finish with."""
        assert self._chert is not None and call.chert_call_id
        loop = asyncio.get_running_loop()
        t = self._t
        deadline = loop.time() + t.ring
        ending_since: float | None = None
        last_status: str | None = None
        saw_dialing = False
        next_agent_check = loop.time() + 2.0
        while True:
            if call.ended.is_set():
                return None, None
            c = await self._safe("Chert GET call", self._chert.get_call(call.chert_call_id))
            now = loop.time()
            if c:
                st = c.get("status")
                if st != last_status:
                    log.info("call %s: FaceTime status %s", call.id, st)
                    last_status = st
                if st in ("dialing", "ringing"):
                    saw_dialing = True
                    await self._ringing(call)
                if st == "active":
                    events = events_of(c)
                    if not events:
                        events = await self._safe("Chert GET events",
                                                  self._chert.get_events(call.chert_call_id)) or []
                    if has_bridge_ready(events):
                        if call.inbound:
                            agent = self._pick_agent(call, await self._participants(call.room) or [],
                                                     require_tracks=False)
                            if agent:
                                call.agent_identity = agent.identity
                            if call.agent_identity is None:
                                # The bridge can be ready before list_participants catches up.
                                if now < deadline:
                                    await self._pause(call, t.poll)
                                    continue
                                return "error", "inbound call never became ready"
                        return None
                if st in TERMINAL:
                    call.chert_terminal = True
                    log.info("call %s: FaceTime ended before live: %s", call.id, terminal_text(c))
                    if looks_unanswered(c) or (saw_dialing and st == "ended"):
                        return "no_answer", None
                    return "error", terminal_text(c)
                if st == "ending":
                    ending_since = ending_since or now
                    if now - ending_since > t.ending_grace:
                        log.warning("call %s: stuck in `ending` for %.0f s before answer", call.id,
                                    now - ending_since)
                        return ("no_answer", None) if saw_dialing else (
                            "error", "the FaceTime call got stuck while ending")
                else:
                    ending_since = None
            if now >= deadline:
                log.info("call %s: not answered within %.0f s", call.id, t.ring)
                if call.inbound:
                    return "error", "inbound call never became ready"
                return "no_answer", None
            if now >= next_agent_check:
                next_agent_check = now + 2.0
                if call.inbound and call.agent_identity is None:
                    agent = self._pick_agent(call, await self._participants(call.room) or [],
                                             require_tracks=False)
                    if agent:
                        call.agent_identity = agent.identity
                elif await self._agent_present(call) is False:
                    return "error", "the helper left before she answered"
            if await self._pause(call, t.poll):
                return None, None

    # ------------------------------------------------------------------ cleanup

    async def _end_chert(self, call: _Call) -> None:
        assert self._chert is not None and call.chert_call_id
        code = await self._safe("Chert end call", self._chert.end_call(call.chert_call_id, f"{call.key_base}-end"))
        if code is None:  # one retry with the same key
            code = await self._safe("Chert end call (retry)",
                                    self._chert.end_call(call.chert_call_id, f"{call.key_base}-end"))
        log.info("call %s: Chert end -> HTTP %s", call.id, code)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._t.end_wait
        st = None
        while loop.time() < deadline:
            c = await self._safe("Chert GET call", self._chert.get_call(call.chert_call_id))
            st = (c or {}).get("status")
            if st in TERMINAL:
                call.chert_terminal = True
                return
            await asyncio.sleep(self._t.poll)
        log.warning("call %s: FaceTime call still %r %.0f s after end; the line may stay busy", call.id, st,
                    self._t.end_wait)

    async def _cleanup(self, call: _Call) -> None:
        """Two bounded phases: hang up the FaceTime call (<= 2 HTTP tries + end_wait), then release
        LiveKit resources (<= t.cleanup). A slow phase can't starve the other."""
        t = self._t
        if call.chert_call_id and not call.chert_terminal and self._chert is not None:
            try:
                await asyncio.wait_for(self._end_chert(call), 2 * t.http + t.end_wait + 2)
            except Exception as e:  # noqa: BLE001
                log.error("call %s: ending the FaceTime call failed (%s)", call.id, type(e).__name__)
        try:
            await asyncio.wait_for(self._release_livekit(call), t.cleanup)
        except Exception as e:  # noqa: BLE001
            log.error("call %s: LiveKit cleanup incomplete (%s)", call.id, type(e).__name__)

    async def _release_livekit(self, call: _Call) -> None:
        rooms = self._rooms
        if rooms is None:
            return
        if call.metadata_set:
            await self._safe("clear room metadata", rooms.set_metadata(call.room, "{}"))
        if call.transport == "kiosk":
            # never remove the kiosk itself: only the helper(s) in its room
            parts = await self._participants(call.room) or []
            for p in parts:
                if p.identity == call.agent_identity or (_is_agent(p) and p.identity != call.room):
                    await self._safe("remove agent", rooms.remove_participant(call.room, p.identity))
        if call.dispatch_id:
            await self._safe("delete dispatch", rooms.delete_dispatch(call.dispatch_id, call.room))
        if call.transport == "facetime" and call.room_created:
            await self._safe("delete room", rooms.delete_room(call.room))
