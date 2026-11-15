"""livekit-agents worker `hearth-agent`: one check-in call per job (contracts/API.md §8).

Order of a job:
  connect -> publish cue card (video) -> start session -> publish agent audio NOW (one track) ->
  stay silent until room metadata says hearth_live -> link her audio + video -> greet (< 2 s) ->
  tools -> goodbye -> POST end -> disconnect.

Privacy: nothing from the call is written anywhere. Logs carry event names and timings only:
never transcript, caption, evidence text or frames.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Literal

from google.genai import types as gt  # imported at process start, not on the first job
from livekit import rtc
from PIL import Image
from livekit.plugins import google
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, function_tool, room_io
from livekit.agents.voice.io import TextOutput, VideoInput

from hearth_contracts import (
    AgentJob,
    ToolConfirmDrink,
    ToolConfirmMeal,
    ToolConfirmMed,
    ToolFlagConcern,
    ToolReportRefusal,
    ToolResult,
)

from . import prompts, sipjudge
from .api import HearthApi
from .budget import BudgetTimer
from .cuecard import CardState, CueCard, decode_photo
from .safety import match_concern

log = logging.getLogger("hearth.agent")

AGENT_NAME = os.environ.get("HEARTH_AGENT_NAME", "hearth-agent")
NOBODY = "__hearth_waiting_for_live__"  # RoomIO links to no one until we say she is live
NO_VIDEO_AFTER = 8.0
KEYWORD_GRACE = 3.0
MOTION_MIN = float(os.environ.get("HEARTH_MOTION_MIN", "1.5"))


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def hung_up_reason() -> str:
    """`hung_up` is not in the contract yet: send `error` until HEARTH_REASON_HUNG_UP=1."""
    return "hung_up" if os.environ.get("HEARTH_REASON_HUNG_UP") == "1" else "error"
PRELIVE_TIMEOUT = float(os.environ.get("HEARTH_PRELIVE_TIMEOUT", "240"))


def parse_job(metadata: str | None) -> AgentJob:
    """Dispatch metadata -> AgentJob. Raises ValueError with a plain reason."""
    if not metadata:
        raise ValueError("dispatch has no metadata")
    try:
        return AgentJob.model_validate_json(metadata)
    except Exception as e:  # pydantic ValidationError or JSON error
        raise ValueError(f"metadata is not an AgentJob: {type(e).__name__}") from e


def is_live(metadata: str | None, checkin_id: str) -> bool:
    try:
        md = json.loads(metadata or "{}")
    except ValueError:
        return False
    if not isinstance(md, dict) or md.get("hearth_live") is not True:
        return False
    cid = md.get("checkin_id")
    return cid is None or cid == checkin_id


def make_model():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return None
    # Optional: HEARTH_MEDIA_RES=high sends more detail per frame. Default leaves the API default (the tested setup).
    res = os.environ.get("HEARTH_MEDIA_RES", "default").strip().lower()
    extra: dict = {}
    if res in {"low", "medium", "high"}:
        extra["media_resolution"] = getattr(gt.MediaResolution, f"MEDIA_RESOLUTION_{res.upper()}")
    try:
        silence_ms = max(300, min(2000, int(os.environ.get("HEARTH_SILENCE_MS", "900"))))
    except ValueError:
        silence_ms = 900
    # One language for speech and for the transcript of her words. HEARTH_LANG=auto lets the model decide.
    lang = os.environ.get("HEARTH_LANG", "en-US").strip()
    lang = "" if lang.lower() == "auto" else lang
    if lang:
        extra["language"] = lang
    return google.realtime.RealtimeModel(
        model=os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live"),
        api_key=key,
        api_version=os.environ.get("GEMINI_LIVE_API_VERSION", "v1beta"),
        voice=os.environ.get("GEMINI_LIVE_VOICE", "Aoede"),
        temperature=0.5,
        input_audio_transcription=(gt.AudioTranscriptionConfig(language_codes=[lang]) if lang
                                   else gt.AudioTranscriptionConfig()),
        output_audio_transcription=gt.AudioTranscriptionConfig(),
        # She speaks slowly and pauses: wait longer before deciding she has finished.
        realtime_input_config=gt.RealtimeInputConfig(
            automatic_activity_detection=gt.AutomaticActivityDetection(
                end_of_speech_sensitivity=gt.EndSensitivity.END_SENSITIVITY_LOW,
                prefix_padding_ms=200,
                silence_duration_ms=silence_ms,
            )
        ),
        # audio+video sessions stop at 2 min without compression (ai.google.dev live-session)
        context_window_compression=gt.ContextWindowCompressionConfig(sliding_window=gt.SlidingWindow()),
        **extra,
    )


# ------------------------------------------------------------------------------------- her video


class HerVideo(VideoInput):
    """Frames from HER camera only, ~1 per second, and only after live. Survives late, muted,
    replaced or missing tracks; accepts any track source (the bridge's may not be 'camera')."""

    def __init__(self, room: rtc.Room, prefix: str, fps: float = 1.0) -> None:
        super().__init__(label="HearthHerVideo")
        self._room = room
        self._prefix = prefix
        self._period = 1.0 / fps
        self._q: asyncio.Queue[rtc.VideoFrame] = asyncio.Queue(maxsize=1)
        self._tasks: dict[str, asyncio.Task] = {}
        self._muted: set[str] = set()
        self.enabled = False
        self.last_frame_at: float | None = None
        self.frames_sent = 0
        self._thumb: bytes | None = None
        self.motion: list[tuple[float, float]] = []  # (time, mean abs change 0-255) per sampled frame
        self.ring: sipjudge.FrameRing | None = None  # a few seconds at ~4 fps for the frame check (memory only)
        room.on("track_subscribed", self._on_sub)
        room.on("track_unsubscribed", self._on_unsub)
        room.on("track_muted", self._on_muted)
        room.on("track_unmuted", self._on_unmuted)

    def attach_existing(self) -> None:
        for p in self._room.remote_participants.values():
            for pub in p.track_publications.values():
                if pub.track is not None:
                    self._on_sub(pub.track, pub, p)

    def _is_hers(self, p: rtc.Participant) -> bool:
        return p.identity.startswith(self._prefix)

    def _on_sub(self, track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        if track.kind != rtc.TrackKind.KIND_VIDEO or not self._is_hers(p) or track.sid in self._tasks:
            return
        log.info("her video track subscribed (name=%s source=%s)", pub.name, pub.source)
        self._tasks[track.sid] = asyncio.create_task(self._consume(track))

    def _on_unsub(self, track: rtc.Track, *_a) -> None:
        t = self._tasks.pop(getattr(track, "sid", ""), None)
        if t:
            log.info("her video track gone")
            t.cancel()

    def _on_muted(self, p: rtc.Participant, pub: rtc.TrackPublication) -> None:
        if self._is_hers(p) and pub.kind == rtc.TrackKind.KIND_VIDEO:
            self._muted.add(pub.sid)
            log.info("her video muted")

    def _on_unmuted(self, p: rtc.Participant, pub: rtc.TrackPublication) -> None:
        if self._is_hers(p) and pub.kind == rtc.TrackKind.KIND_VIDEO:
            self._muted.discard(pub.sid)
            log.info("her video unmuted")

    async def _consume(self, track: rtc.Track) -> None:
        stream = rtc.VideoStream(track, capacity=1)
        next_at = 0.0
        try:
            async for ev in stream:
                now = time.monotonic()
                if not self.enabled or track.sid in self._muted:
                    continue
                if self.ring is not None and self.ring.due(now):
                    self._to_ring(ev.frame, now)
                if now < next_at:
                    continue
                next_at = now + self._period
                self.last_frame_at = now
                self._note_motion(ev.frame, now)
                if self._q.full():
                    self._q.get_nowait()
                self._q.put_nowait(self._shrink(ev.frame))
        except asyncio.CancelledError:
            pass
        finally:
            await stream.aclose()

    @staticmethod
    def _shrink(frame: rtc.VideoFrame) -> rtc.VideoFrame:
        """Send the model less of her home: longest side at most HEARTH_FRAME_MAX_SIDE px (0 = as received).
        In memory only; on any problem the frame goes through unchanged."""
        try:
            limit = int(os.environ.get("HEARTH_FRAME_MAX_SIDE", "512"))
            longest = max(frame.width, frame.height)
            if limit <= 0 or longest <= limit:
                return frame
            scale = limit / longest
            w, h = max(2, int(frame.width * scale) // 2 * 2), max(2, int(frame.height * scale) // 2 * 2)
            rgba = frame if frame.type == rtc.VideoBufferType.RGBA else frame.convert(rtc.VideoBufferType.RGBA)
            img = Image.frombytes("RGBA", (rgba.width, rgba.height), bytes(rgba.data)).resize((w, h))
            return rtc.VideoFrame(w, h, rtc.VideoBufferType.RGBA, img.tobytes())
        except Exception:  # noqa: BLE001
            return frame

    def _to_ring(self, frame: rtc.VideoFrame, now: float) -> None:
        try:
            rgba = frame if frame.type == rtc.VideoBufferType.RGBA else frame.convert(rtc.VideoBufferType.RGBA)
            self.ring.add_image(Image.frombytes("RGBA", (rgba.width, rgba.height), bytes(rgba.data)), now)
        except Exception as e:  # noqa: BLE001
            log.debug("frame ring skipped a frame: %s", type(e).__name__)

    def _note_motion(self, frame: rtc.VideoFrame, now: float) -> None:
        """A 32x24 grey thumbnail, in memory only, to tell a moving scene from a still picture."""
        try:
            rgba = frame.convert(rtc.VideoBufferType.RGBA)
            small = Image.frombytes("RGBA", (rgba.width, rgba.height), bytes(rgba.data)).convert("L").resize((32, 24))
            cur = small.tobytes()
            if self._thumb is not None:
                diff = sum(abs(a - b) for a, b in zip(cur, self._thumb)) / len(cur)
                self.motion.append((now, diff))
                self.motion = self.motion[-60:]
            self._thumb = cur
        except Exception:  # noqa: BLE001
            pass

    def max_motion(self, window: float = 25.0) -> float | None:
        now = time.monotonic()
        vals = [m for t, m in self.motion if now - t <= window]
        return max(vals) if vals else None

    def seeing(self, within: float = 4.0) -> bool:
        return self.last_frame_at is not None and time.monotonic() - self.last_frame_at < within

    async def __anext__(self) -> rtc.VideoFrame:
        frame = await self._q.get()
        self.frames_sent += 1
        return frame

    async def aclose(self) -> None:
        for t in self._tasks.values():
            t.cancel()


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def current_sentence(buf: str) -> str:
    """The sentence being spoken now, from its start (the card pages it if it is long)."""
    parts = [p for p in _SENTENCE_END.split(buf.strip()) if p.strip()]
    return parts[-1].strip() if parts else ""


class CaptionSink(TextOutput):
    """Receives the helper's words as they are spoken (synced to audio) for the card + captions."""

    def __init__(self, call: Call) -> None:
        super().__init__(label="HearthCaptions", next_in_chain=None)
        self._call = call
        self._buf = ""

    async def capture_text(self, text: str) -> None:
        self._buf += text
        self._call.card.update(caption=current_sentence(self._buf))

    def flush(self) -> None:
        sentence, self._buf = self._buf.strip(), ""
        if sentence and not self._call.api.ended:
            self._call.spawn(self._call.api.caption("helper", sentence, True))


# ------------------------------------------------------------------------------------- the call


class Call:
    def __init__(self, ctx: JobContext, job: AgentJob, api: HearthApi, card: CueCard) -> None:
        self.ctx = ctx
        self.job = job
        self.api = api
        self.card = card
        self.session: AgentSession | None = None
        self.video: HerVideo | None = None
        self.live = asyncio.Event()
        self.live_at: float | None = None
        self.first_speech_at: float | None = None
        self.asked = False
        self.clamped: str | None = None
        self.last_tool_at = 0.0  # last outcome tool (confirm / refusal / concern)
        self.last_speech_start = 0.0
        self.outcome: str | None = None
        self.concern_by_model = False
        self.keyword_task: asyncio.Task | None = None
        self.ending = False
        self.budget: BudgetTimer | None = None
        self._bg: set[asyncio.Task] = set()
        self.heard_her = False  # any non-empty transcript of her speech since live
        self.no_answer_ask_s = _env_float("HEARTH_NO_ANSWER_ASK_S", 20.0)
        self.no_answer_end_s = _env_float("HEARTH_NO_ANSWER_END_S", 40.0)
        self.agent_state = "initializing"
        self.verdict: sipjudge.Verdict | None = None  # latest frame check
        self.positive: sipjudge.Verdict | None = None  # latest frame check that saw a drink
        self.checks = 0
        self.nudged_at = 0.0
        self._check_lock = asyncio.Lock()

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    # -- seeing
    def can_see(self) -> bool:
        return self.video is not None and self.video.seeing()

    def scene_moved(self) -> bool:
        """False when the picture has been (near) still for the whole recent window: a held cup
        or a still photo, not a drink. Threshold from HEARTH_MOTION_MIN (mean grey change 0-255)."""
        m = self.video.max_motion() if self.video else None
        log.info("motion check: max change %.2f (min %.2f)", m if m is not None else -1, MOTION_MIN)
        return m is not None and m >= MOTION_MIN

    def clamp_unseen(self, confidence: float, evidence: str) -> tuple[float, str]:
        """No picture of her, or a picture that never moved => no visual confirmation: only what
        she SAID may be logged, as a low-confidence note (Said: reports complete as reported)."""
        self.clamped = None
        if self.can_see() and self.scene_moved():
            return confidence, evidence
        self.clamped = "still" if self.can_see() else "no_video"
        ev = evidence if evidence.lower().startswith("said:") else f"Said: {evidence}"
        if self.clamped == "no_video" and ev.lower().startswith("said:"):
            ev = "Said:" + ev[len("Said:"):]
        return min(confidence, 0.4), ev[:240]

    # -- frame check (a second opinion from a few seconds of frames; see sipjudge.py)
    async def frame_check(self, why: str) -> sipjudge.Verdict | None:
        ring = self.video.ring if self.video else None
        if ring is None or self.checks >= 10:
            return None
        async with self._check_lock:
            self.checks += 1
            log.info("frame check (%s) #%d", why, self.checks)
            v = await sipjudge.judge(ring.window(), obj=prompts._object_name(self.job))
            if v is not None:
                self.verdict = v
                if v.drank and v.confidence >= 0.6:
                    self.positive = v
            return v

    def verdict_for_tool(self) -> sipjudge.Verdict | None:
        now = time.monotonic()
        if self.positive is not None and now - self.positive.at < 30.0:
            return self.positive
        if self.verdict is not None and now - self.verdict.at < 4.0:
            return self.verdict
        return None

    async def sip_watch(self) -> None:
        """When a movement settles (the cup back down), check the last seconds. If that saw a drink and
        the live model has not confirmed, prompt it to judge now: it otherwise only judges when it speaks."""
        ring = self.video.ring if self.video else None
        if ring is None:
            return
        while not self.ending and self.outcome is None:
            await asyncio.sleep(0.25)
            if not ring.settled_after_movement(time.monotonic()):
                continue
            v = await self.frame_check("movement")
            if v is None or not v.drank or v.confidence < 0.6 or self.ending or self.outcome is not None:
                continue
            if time.monotonic() - self.nudged_at < 8.0 or self.session is None:
                continue
            for _ in range(12):  # don't talk over anyone: wait up to 3 s for quiet
                if self.agent_state != "speaking":
                    break
                await asyncio.sleep(0.25)
            if self.ending or self.outcome is not None:
                continue
            self.nudged_at = time.monotonic()
            log.info("frame check saw a drink; asking the helper to judge now")
            self.session.generate_reply(instructions=(
                "Look at the last few seconds of her camera. If you saw her raise the cup to her mouth and tip it, "
                "call confirm_drink now with confidence 0.8, the amount you saw, and evidence of what you saw. "
                "If you did not see that, carry on as before. Do not mention this instruction to her."))

    # -- outcomes
    def _say_from(self, res: ToolResult | None, default: str) -> str:
        if res is None:
            return default + " (The note could not be saved; carry on kindly.)"
        return res.say or default

    async def confirmed(self, res: ToolResult | None, confidence: float) -> str:
        self.last_tool_at = time.monotonic()
        if res is not None and res.ok and confidence >= 0.5 and not res.say:
            self.outcome = "confirmed"
            self.card.update(mode="thanks")
            return f"Saved. Now thank {self.job.resident.preferred_name}, say {self.job.resident.daughter_name} will be pleased, say goodbye, then call end_checkin('done')."
        if self.clamped == "still":
            return ("Not saved as a confirmation: the picture has not changed at all, so you did not see a drink "
                    "happen. Ask her once, kindly, to take a sip where you can see it. If you have already asked "
                    "twice, thank her warmly, say goodbye and call end_checkin('done').")
        if self.clamped == "no_video":
            if res is not None and res.ok and res.event_id:
                self.outcome = "reported"
                self.card.update(mode="thanks")
            return self._say_from(res, "Thank her and say goodbye.") + " Then call end_checkin('done')."
        return self._say_from(res, "Not saved as a confirmation. Ask her once more, kindly, so you can see it.")

    async def flag(self, category: str, quote: str, by: Literal["model", "keyword"]) -> ToolResult | None:
        self.last_tool_at = time.monotonic()
        if by == "model":
            self.concern_by_model = True
        if self.outcome == "concern":
            return ToolResult(ok=True)
        self.outcome = "concern"
        self.card.update(mode="concern", line=f"{self.job.resident.daughter_name} is coming. Stay where you are.")
        log.info("concern flagged by %s category=%s", by, category)
        return await self.api.flag_concern(
            ToolFlagConcern(category=category, evidence_quote=quote[:200], detected_by=by)  # type: ignore[arg-type]
        )

    # -- her words (keyword backup + captions)
    def on_her_transcript(self, text: str, final: bool) -> None:
        if not self.live.is_set() or self.ending:
            return
        if text.strip():
            self.heard_her = True
        if final:
            self.spawn(self.api.caption("her", text, True))
        if self.outcome == "concern" or self.keyword_task is not None:
            return
        cat = match_concern(text)
        if cat:
            log.info("keyword backup armed category=%s", cat)
            self.keyword_task = self.spawn(self._keyword_backup(cat, text))

    async def _keyword_backup(self, category: str, text: str) -> None:
        await asyncio.sleep(KEYWORD_GRACE)
        if self.concern_by_model or self.outcome == "concern":
            log.info("keyword backup: model already flagged")
            return
        await self.flag(category, text, "keyword")
        if self.session:
            line = prompts.CONCERN_LINE.replace("Sarah", self.job.resident.daughter_name)
            self.session.generate_reply(
                instructions=f'Say exactly: "{line}" Then stay with her calmly in one or two short sentences.'
            )
        await asyncio.sleep(14)
        await self.finish("concern")

    # -- lifecycle
    async def go_live(self) -> None:
        if self.live.is_set():
            return
        self.live_at = time.monotonic()
        self.live.set()
        log.info("live: linking her and greeting")
        assert self.session is not None
        her = await self._find_her(timeout=0)  # never delay the greeting: link late if needed
        if her is not None:
            self.session.room_io.set_participant(her.identity)
        else:
            log.warning("live but no participant with prefix %s yet", self.job.caller_identity_prefix)
        if self.video:
            self.video.enabled = True
            if self.video.ring is not None:
                self.spawn(self.sip_watch())
        self.card.update(mode="asking", caption="")
        opening = prompts.opening_line(self.job)
        self.session.generate_reply(instructions=f'Begin the call now. Say: "{opening}" Then make the first request.')
        self.spawn(self.api.status("connected"))
        self.budget = BudgetTimer(self.job.max_seconds, self._budget_warn, self._budget_expire, warn_before=10)
        self.budget.start()
        self.spawn(self._video_check())
        self.spawn(self.no_answer_watch())
        if her is None:
            self.spawn(self._link_late())

    def nobody_there(self) -> bool:
        """Auto-answered but no person: she never spoke and no outcome tool was called."""
        return not self.heard_her and self.last_tool_at == 0.0 and self.outcome is None and not self.ending

    async def no_answer_watch(self) -> None:
        """The kitchen iPad auto-answers, so an empty kitchen looks like a connected call. Deterministic:
        ask once at ~20 s, end as no_answer at ~40 s (the core then retries once, then tells Sarah)."""
        name = self.job.resident.preferred_name
        await asyncio.sleep(self.no_answer_ask_s)
        if not self.nobody_there():
            return
        log.info("no answer: nothing heard %.0fs after live; asking once", self.no_answer_ask_s)
        if self.session:
            self.session.generate_reply(instructions=f'Say exactly: "{name}, are you there?" Then wait.')
        await asyncio.sleep(max(0.0, self.no_answer_end_s - self.no_answer_ask_s))
        if not self.nobody_there():
            return
        log.info("no answer: nothing heard %.0fs after live; signing off", self.no_answer_end_s)
        self.card.update(mode="okay", line="I'll try again a little later")
        if self.session:
            self.session.generate_reply(
                instructions='Say exactly: "I\'ll try again a little later. Goodbye for now." Say nothing else.')
        await self.finish("no_answer", "no speech heard after live")

    async def _link_late(self) -> None:
        her = await self._find_her(timeout=30)
        if her is not None and self.session:
            self.session.room_io.set_participant(her.identity)

    async def _find_her(self, timeout: float) -> rtc.RemoteParticipant | None:
        deadline = time.monotonic() + timeout
        while True:
            for p in self.ctx.room.remote_participants.values():
                if p.identity.startswith(self.job.caller_identity_prefix):
                    return p
            if time.monotonic() > deadline:
                return None
            await asyncio.sleep(0.2)

    async def _video_check(self) -> None:
        await asyncio.sleep(NO_VIDEO_AFTER)
        if self.ending or self.can_see():
            log.info("video ok at %.0fs (frames=%s)", NO_VIDEO_AFTER, self.video.frames_sent if self.video else 0)
            return
        log.info("no video %.0fs after live", NO_VIDEO_AFTER)
        if self.session:
            self.session.generate_reply(
                instructions=prompts.no_video_instructions(self.job)
            )

    def spoke_since_tool(self) -> bool:
        return self.last_speech_start > self.last_tool_at

    def on_agent_state(self, old: str, new: str) -> None:
        now = time.monotonic()
        self.agent_state = new
        if new == "speaking":
            self.last_speech_start = now
        if new == "speaking" and self.first_speech_at is None and self.live_at is not None:
            self.first_speech_at = now
            log.info("first speech %.2fs after live", now - self.live_at)
        if old == "speaking" and new != "speaking" and self.live.is_set() and not self.asked:
            self.asked = True
            if self.card.state.mode == "asking":
                self.card.update(mode="watching")
            self.spawn(self.api.status("watching"))

    async def _budget_warn(self) -> None:
        if self.ending or not self.session:
            return
        self.session.generate_reply(instructions="Time is up. Say one short, warm goodbye sentence now.")

    async def _budget_expire(self) -> None:
        await self.finish("time_budget", wait_speech=False)

    async def _wait_quiet(self, max_wait: float, expect_speech: bool) -> None:
        """Let the goodbye play out: if a goodbye is still to come, wait for it to start (up to 5 s),
        then wait until the helper has stopped speaking for a moment."""
        t0 = time.monotonic()
        deadline = t0 + max_wait
        if expect_speech:
            while time.monotonic() - t0 < min(5.0, max_wait) and self.last_speech_start < t0:
                await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        quiet_since = None
        while time.monotonic() < deadline:
            speaking = self.session is not None and self.session.agent_state == "speaking"
            if speaking:
                quiet_since = None
            else:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since > 1.2:
                    return
            await asyncio.sleep(0.2)

    async def finish(self, reason: str, detail: str = "", wait_speech: bool = True) -> None:
        if self.ending:
            return
        self.ending = True
        log.info("finishing reason=%s", reason)
        if self.budget:
            self.budget.cancel()
        if wait_speech:  # goodbye first: ending the check-in hangs up the call
            max_wait = 10.0
            if self.live_at is not None:  # never past the hard budget
                max_wait = min(max_wait, self.live_at + self.job.max_seconds - time.monotonic())
            if max_wait > 0:
                await self._wait_quiet(max_wait, expect_speech=not self.spoke_since_tool())
        await self.api.end(reason, detail)
        self.ctx.shutdown(reason=f"checkin {reason}")


# ------------------------------------------------------------------------------------- the agent


def build_agent(call: Call) -> Agent:
    job = call.job

    class Helper(Agent):
        def __init__(self) -> None:
            super().__init__(instructions=prompts.instructions(job))

        @function_tool
        async def confirm_drink(self, amount: Literal["sip", "some", "most", "all"], confidence: float,
                                evidence: str) -> str:
            """Log that you SAW her drink. Only after you watched the cup go to her mouth.

            Args:
                amount: how much she drank, from what you saw: sip, some, most or all.
                confidence: 0 to 1, how sure you are from what you SAW.
                evidence: short description of what you saw, e.g. "held up blue mug, two drinks". Never "swallowed".
            """
            c, ev = call.clamp_unseen(max(0.0, min(1.0, confidence)), evidence)
            if call.clamped is None and call.video is not None and call.video.ring is not None:
                v = call.verdict_for_tool() or await call.frame_check("tool")
                before = c
                c, ev, why = sipjudge.combine(c, ev, v)
                log.info("frame check on confirm: %s %.2f -> %.2f", why, before, c)
            res = await call.api.confirm_drink(ToolConfirmDrink(amount=amount, confidence=c, evidence=ev[:240]))
            log.info("tool confirm_drink amount=%s confidence=%.2f seen=%s clamped=%s", amount, c, call.can_see(), call.clamped)
            return await call.confirmed(res, c)

        @function_tool
        async def confirm_meal(self, amount: Literal["sip", "some", "most", "all"], confidence: float,
                               evidence: str) -> str:
            """Log that you SAW her eating her meal.

            Args:
                amount: how much she ate, from what you saw: sip, some, most or all.
                confidence: 0 to 1, how sure you are from what you SAW.
                evidence: short description of what you saw on the plate and her eating.
            """
            c, ev = call.clamp_unseen(max(0.0, min(1.0, confidence)), evidence)
            res = await call.api.confirm_meal(ToolConfirmMeal(amount=amount, confidence=c, evidence=ev[:240]))
            log.info("tool confirm_meal amount=%s confidence=%.2f seen=%s clamped=%s", amount, c, call.can_see(), call.clamped)
            return await call.confirmed(res, c)

        @function_tool
        async def confirm_med(
            self,
            steps_seen: list[Literal["slot_opened", "pill_in_hand", "hand_to_mouth", "drank_water", "slot_shown_empty"]],
            confidence: float,
            evidence: str,
        ) -> str:
            """Log that you OBSERVED her taking her tablets from the right slot. Only the steps you saw.

            Args:
                steps_seen: the steps you actually saw, in order.
                confidence: 0 to 1, how sure you are from what you SAW.
                evidence: short description of what you saw. Say "observed taking", never "swallowed".
            """
            c, ev = call.clamp_unseen(max(0.0, min(1.0, confidence)), evidence)
            res = await call.api.confirm_med(ToolConfirmMed(steps_seen=steps_seen, confidence=c, evidence=ev[:240]))
            log.info("tool confirm_med steps=%d confidence=%.2f seen=%s clamped=%s", len(steps_seen), c, call.can_see(), call.clamped)
            return await call.confirmed(res, c)

        @function_tool
        async def report_refusal(self, said: str, asked_times: int) -> str:
            """She declined, or hadn't done it after two kind asks. Never ask a third time.

            Args:
                said: the gist of what she said, e.g. "not thirsty, had tea earlier".
                asked_times: how many times you asked (1 or 2).
            """
            res = await call.api.report_refusal(ToolReportRefusal(said=said[:200], asked_times=max(1, min(2, asked_times))))
            log.info("tool report_refusal asked_times=%s", asked_times)
            call.last_tool_at = time.monotonic()
            if call.outcome is None:
                call.outcome = "refused"
            call.card.update(mode="okay", line="That's all right")
            d = job.resident.daughter_name
            return call._say_from(res, f"Saved. Say \"That's all right, I'll let {d} know\", say goodbye, then call end_checkin('refused').")

        @function_tool
        async def flag_concern(
            self,
            category: Literal["said_fell_or_hurt", "asked_for_help", "reports_pain_or_unwell", "not_responding",
                              "on_floor_or_out_of_view"],
            evidence_quote: str,
        ) -> str:
            """Safety: she fell or is hurt, asks for help, reports pain or feeling unwell, stops responding,
            or is on the floor / out of view. Call at once.

            Args:
                category: the closest category.
                evidence_quote: her words, or what you saw.
            """
            res = await call.flag(category, evidence_quote, "model")
            line = prompts.CONCERN_LINE.replace("Sarah", job.resident.daughter_name)
            return call._say_from(res, f'{job.resident.daughter_name} is being told now. Say exactly: "{line}" '
                                       "Stay with her briefly, calmly, then call end_checkin('concern').")

        @function_tool
        async def end_checkin(
            self,
            reason: Literal["done", "refused", "no_answer", "concern", "distressed", "time_budget", "error"],
        ) -> str:
            """End the call. Only after you have said goodbye.

            Args:
                reason: done, refused, concern, distressed, no_answer, time_budget or error.
            """
            log.info("tool end_checkin reason=%s", reason)
            if call.ending:  # the worker is already ending the call (no-answer, budget, keyword backup)
                return "The call is ending. Say nothing more."
            said_goodbye = call.spoke_since_tool()
            call.spawn(call.finish(reason))
            if said_goodbye:
                return "The call is ending. Say nothing more."
            return "The call is ending. Say your short, warm goodbye now, in one sentence."

    return Helper()


# ------------------------------------------------------------------------------------- entrypoint


def _publish_audio_early(session: AgentSession) -> asyncio.Task | None:
    """The FaceTime bridge is only dialled once our participant shows ONE unmuted audio track next
    to its video. RoomIO normally publishes agent audio when a remote participant is linked, which
    we hold back until live. So start that same audio output now, and make RoomIO's own later
    start() a no-op: the same single track is reused and a second one is never published."""
    out = getattr(session.room_io, "_audio_output", None)
    if out is None:
        log.warning("no RoomIO audio output to publish early")
        return None
    original = out.start
    state = {"started": False}

    async def start_once(*a, **k) -> None:
        if state["started"]:
            return
        state["started"] = True
        await original(*a, **k)

    out.start = start_once  # type: ignore[method-assign]
    return asyncio.create_task(out.start(), name="hearth-audio-publish")


def _fresh_session_on_error(session: AgentSession) -> None:
    """Gemini Live sometimes drops a session with 1011 "Internal error". The plugin then reconnects
    with the session-resumption handle, which the server has already forgotten (1008 "session not
    found"), and the call dies. Ask the plugin for a FRESH session instead: it replays our chat
    context into it, so the conversation carries on after a short pause.
    TODO(upstream): uses the plugin's private _fresh_session_needed flag (livekit-plugins-google 1.8.12)."""
    rt = getattr(getattr(session, "_activity", None), "_rt_session", None)
    if rt is None or not hasattr(rt, "_fresh_session_needed"):
        log.warning("cannot hook Gemini session errors; a dropped session will end the call")
        return

    def _on_error(ev) -> None:
        if getattr(ev, "recoverable", False):
            log.warning("Gemini session dropped (%s); reconnecting with a fresh session",
                        type(getattr(ev, "error", None)).__name__)
            rt._fresh_session_needed = True

    rt.on("error", _on_error)


async def _wait_audio_published(session: AgentSession, timeout: float = 6.0) -> bool:
    out = getattr(session.room_io, "_audio_output", None)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(out, "_publication", None) is not None:
            return True
        await asyncio.sleep(0.05)
    return False


server = AgentServer(
    # Rendering + encoding the cue card pushes CPU past the default threshold and would mark the
    # worker unavailable; one call at a time is all we need.
    load_threshold=float(os.environ.get("HEARTH_LOAD_THRESHOLD", "0.95")),
    num_idle_processes=int(os.environ.get("HEARTH_IDLE_PROCESSES", "1")),
    # health HTTP port; set HEARTH_WORKER_PORT when a second worker shares the container
    **({"port": int(os.environ["HEARTH_WORKER_PORT"])} if os.environ.get("HEARTH_WORKER_PORT") else {}),
)


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    t0 = time.monotonic()
    try:
        job = parse_job(ctx.job.metadata)
    except ValueError as e:
        log.error("bad job: %s", e)
        ctx.shutdown(reason="bad job metadata")
        return

    api = HearthApi(job.api_base, job.checkin_id)
    r = job.resident
    card = CueCard(CardState(
        mode="waiting", kind=job.kind, name=r.preferred_name, helper_label=f"{r.daughter_name}'s helper",
        line=(job.cue.line if job.cue else ""), slot_label=(job.cue.slot_label if job.cue else None),
    ))
    call = Call(ctx, job, api, card)

    async def _on_shutdown(reason: str = "") -> None:
        if not api.ended:
            await api.end("error", "agent stopped before an outcome" if call.outcome is None else "")
        await card.aclose()
        if call.video:
            await call.video.aclose()
        await api.aclose()
        log.info("job closed live=%s outcome=%s", call.live.is_set(), call.outcome)

    ctx.add_shutdown_callback(_on_shutdown)

    try:
        model = make_model()
        if model is None:
            log.error("GEMINI_API_KEY missing: cannot run the helper; ending the check-in")
            await api.end("error", "agent has no model key")
            ctx.shutdown(reason="no model key")
            return

        # cue photo, fetched once into memory (never to disk)
        photo_task = asyncio.create_task(_fetch_photo(job))

        call.video = HerVideo(ctx.room, job.caller_identity_prefix)  # before connect: no event missed
        if job.kind == "drink" and sipjudge.enabled():
            call.video.ring = sipjudge.FrameRing()
        await ctx.connect()
        await card.publish(ctx.room)
        log.info("cue card published at %.2fs", time.monotonic() - t0)

        session = AgentSession(llm=model, video_sampler=None, user_away_timeout=None)
        call.session = session
        session.on("agent_state_changed", lambda ev: call.on_agent_state(ev.old_state, ev.new_state))
        session.on("user_input_transcribed", lambda ev: call.on_her_transcript(ev.transcript, ev.is_final))
        session.on("user_state_changed", lambda ev: log.info("her state %s -> %s", ev.old_state, ev.new_state))

        def _on_close(ev) -> None:
            log.info("session closed reason=%s error=%s", getattr(ev, "reason", None),
                     type(ev.error).__name__ if getattr(ev, "error", None) else None)
            if not call.ending:
                by_outcome = {"confirmed": "done", "reported": "done", "refused": "refused", "concern": "concern"}
                reason = by_outcome.get(call.outcome or "", "error")
                if reason == "error" and "PARTICIPANT_DISCONNECTED" in str(getattr(ev, "reason", "")):
                    reason = hung_up_reason()
                call.spawn(call.finish(reason, f"session closed: {getattr(ev, 'reason', '')}"[:200], False))

        session.on("close", _on_close)

        await session.start(
            agent=build_agent(call),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                participant_identity=NOBODY,
                video_input=False,  # we feed HerVideo ourselves (any source, gated, 1 fps)
                text_output=room_io.TextOutputOptions(next_in_chain=CaptionSink(call)),
                close_on_disconnect=True,
            ),
            record=False,  # never record audio, transcripts or traces
            session_host=False,  # don't stream session state/events into the room
        )
        _fresh_session_on_error(session)
        _publish_audio_early(session)
        ok = await _wait_audio_published(session)
        log.info("agent audio published early=%s at %.2fs", ok, time.monotonic() - t0)
        session.input.video = call.video
        call.video.attach_existing()

        photo = await photo_task
        if photo is not None:
            card.update(photo=photo)

        if not await _wait_for_live(ctx.room, api, job):
            await call.finish("no_answer", "never went live", wait_speech=False)
            return
        await call.go_live()
    except Exception as e:
        log.exception("agent crashed")
        await api.end("error", f"agent crashed: {type(e).__name__}")
        ctx.shutdown(reason="crash")


async def _fetch_photo(job: AgentJob):
    if not job.cue or not job.cue.image_url:
        return None
    import httpx

    url = job.cue.image_url
    if url.startswith("/"):
        url = job.api_base.rstrip("/") + url
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(url)
            r.raise_for_status()
            return await asyncio.to_thread(decode_photo, r.content)
    except Exception as e:  # noqa: BLE001
        log.warning("cue photo fetch failed: %s", type(e).__name__)
        return None


async def _wait_for_live(room: rtc.Room, api: HearthApi, job: AgentJob) -> bool:
    """Room metadata {"hearth_live": true} is the go signal; fallback: the API says connected.
    Returns False if the check-in ended (or timed out) before it went live."""
    done: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    def _set(v: bool) -> None:
        if not done.done():
            done.set_result(v)

    if is_live(room.metadata, job.checkin_id):
        _set(True)

    def _changed(_old: str, new: str) -> None:
        if is_live(new, job.checkin_id):
            log.info("live via room metadata")
            _set(True)

    room.on("room_metadata_changed", _changed)

    async def _poll() -> None:
        while not done.done():
            c = await api.get_checkin()
            if c is not None and c.status in ("connected", "watching"):
                log.info("live via API poll")
                _set(True)
            elif c is not None and c.status == "ended":
                log.info("check-in ended before live")
                _set(False)
            await asyncio.sleep(1.0)

    poller = asyncio.create_task(_poll())
    try:
        return await asyncio.wait_for(done, PRELIVE_TIMEOUT)
    except asyncio.TimeoutError:
        log.info("never went live within %.0fs", PRELIVE_TIMEOUT)
        return False
    finally:
        poller.cancel()
        room.off("room_metadata_changed", _changed)


def main() -> None:
    cli.run_app(server)  # configures logging itself


if __name__ == "__main__":
    main()
