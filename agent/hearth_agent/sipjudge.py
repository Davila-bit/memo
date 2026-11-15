"""A second opinion on "did she drink?", from a short run of frames instead of one picture a second.

The live model sees about one frame per second and only judges when something makes it speak, so a
quick sip can pass between frames or go unjudged. This keeps the last few seconds of HER camera at a
few frames per second, in memory only (never on disk, never logged), and when a movement settles, or
when the live model calls confirm_drink, it sends that short sequence to a Gemini Flash model, which
reports what it saw step by step: lifted, at mouth, tilted, lowered.

Off with HEARTH_VERIFY=0. Any error or missing key: no verdict, and the call carries on as before.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from dataclasses import dataclass

from PIL import Image

log = logging.getLogger("hearth.agent")


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def enabled() -> bool:
    return os.environ.get("HEARTH_VERIFY", "1").strip() not in {"0", "false", "off", ""}


@dataclass
class Verdict:
    drank: bool
    lifted: bool
    at_mouth: bool
    tilted: bool
    lowered: bool
    confidence: float
    at: float  # time.monotonic() of the verdict
    ms: int  # how long the check took


class FrameRing:
    """The last few seconds of her camera, small JPEGs at a few frames per second. Memory only."""

    def __init__(self, fps: float | None = None, seconds: float | None = None, side: int | None = None) -> None:
        self.period = 1.0 / (fps or _f("HEARTH_VERIFY_FPS", 4.0))
        self.seconds = seconds or _f("HEARTH_VERIFY_SECONDS", 10.0)
        self.side = int(side or _f("HEARTH_VERIFY_SIDE", 448))
        self.frames: list[tuple[float, bytes]] = []
        self.motion: list[tuple[float, float]] = []
        self._thumb: bytes | None = None
        self._next = 0.0
        self._burst_seen_at = float("-inf")  # last movement already reported

    def due(self, now: float) -> bool:
        return now >= self._next

    def add_image(self, img: Image.Image, now: float) -> None:
        """img: any PIL image of her camera at `now`."""
        self._next = now + self.period
        grey = img.convert("L").resize((32, 24))
        cur = grey.tobytes()
        if self._thumb is not None:
            self.motion.append((now, sum(abs(a - b) for a, b in zip(cur, self._thumb)) / len(cur)))
        self._thumb = cur
        small = img.convert("RGB")
        small.thumbnail((self.side, self.side))
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=72)
        self.frames.append((now, buf.getvalue()))
        cutoff = now - self.seconds
        self.frames = [f for f in self.frames if f[0] >= cutoff]
        self.motion = [m for m in self.motion if m[0] >= cutoff]

    def window(self, seconds: float | None = None, max_frames: int = 16) -> list[tuple[float, bytes]]:
        if not self.frames:
            return []
        end = self.frames[-1][0]
        fr = [f for f in self.frames if f[0] >= end - (seconds or self.seconds)]
        if len(fr) <= max_frames:
            return fr
        step = (len(fr) - 1) / (max_frames - 1)
        return [fr[round(i * step)] for i in range(max_frames)]

    def settled_after_movement(self, now: float, hi: float | None = None, lo: float | None = None,
                               calm_s: float = 0.75) -> bool:
        """True once per movement: something moved a lot (a cup going up), then the picture has been
        calm for `calm_s` (the cup is back down). The moment to ask "was that a drink?"."""
        hi = hi if hi is not None else _f("HEARTH_VERIFY_BURST", 6.0)
        lo = lo if lo is not None else _f("HEARTH_VERIFY_CALM", 3.0)
        recent = [(t, m) for t, m in self.motion if now - t <= 6.0]
        big = [t for t, m in recent if m >= hi and t > self._burst_seen_at]
        if not big:
            return False
        last_big = big[-1]
        calm = [m for t, m in recent if t > last_big]
        if now - last_big >= calm_s and calm and max(calm) < lo:
            self._burst_seen_at = last_big
            return True
        return False


PROMPT = """These are {n} frames from a kitchen camera, in time order over the last {secs:.0f} seconds (about
{fps:.0f} per second). An older person was asked to have a drink from {obj}.
Look at every frame. For each of these, answer only from what is visible:
- lifted: a cup, mug or glass is raised from table or chest height toward the face.
- at_mouth: the rim of the cup touches or is right at the mouth.
- tilted: the cup is tipped up at the mouth (the bottom rises; the head may tip back) as when drinking.
- lowered: afterwards the cup comes back down, away from the mouth.
drank = true only if at_mouth AND tilted were both seen. A cup only held still near the face, or only
shown to the camera, is NOT a drink. confidence = how sure you are that your drank answer is right (0 to 1)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "lifted": {"type": "boolean"},
        "at_mouth": {"type": "boolean"},
        "tilted": {"type": "boolean"},
        "lowered": {"type": "boolean"},
        "drank": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": ["lifted", "at_mouth", "tilted", "lowered", "drank", "confidence"],
}

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai

        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            return None
        _client = genai.Client(api_key=key)
    return _client


async def judge(frames: list[tuple[float, bytes]], obj: str = "a cup", timeout: float | None = None) -> Verdict | None:
    """Ask Gemini Flash about a short frame sequence. None on any problem (the call carries on)."""
    if len(frames) < 3:
        return None
    client = _get_client()
    if client is None:
        return None
    from google.genai import types as gt

    t0 = time.monotonic()
    secs = max(1.0, frames[-1][0] - frames[0][0])
    parts: list = [PROMPT.format(n=len(frames), secs=secs, fps=len(frames) / secs, obj=obj)]
    for t, jpg in frames:
        parts.append(f"t=+{t - frames[0][0]:.1f}s")
        parts.append(gt.Part.from_bytes(data=jpg, mime_type="image/jpeg"))
    cfg = gt.GenerateContentConfig(
        temperature=0.0, response_mime_type="application/json", response_schema=SCHEMA,
        media_resolution=gt.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    )
    model = os.environ.get("HEARTH_VERIFY_MODEL", "gemini-3.5-flash-lite")
    level = os.environ.get("HEARTH_VERIFY_THINKING", "minimal").strip().upper()
    if level and level != "DEFAULT":
        cfg.thinking_config = gt.ThinkingConfig(thinking_level=getattr(gt.ThinkingLevel, level, gt.ThinkingLevel.MINIMAL))
    try:
        r = await asyncio.wait_for(
            client.aio.models.generate_content(model=model, contents=parts, config=cfg),
            timeout or _f("HEARTH_VERIFY_TIMEOUT", 6.0),
        )
        d = r.parsed if isinstance(r.parsed, dict) else __import__("json").loads(r.text)
        v = Verdict(
            drank=bool(d["drank"]) and bool(d["at_mouth"]) and bool(d["tilted"]),
            lifted=bool(d["lifted"]), at_mouth=bool(d["at_mouth"]), tilted=bool(d["tilted"]),
            lowered=bool(d["lowered"]), confidence=max(0.0, min(1.0, float(d["confidence"]))),
            at=time.monotonic(), ms=int((time.monotonic() - t0) * 1000),
        )
        log.info("frame check: drank=%s lifted=%s at_mouth=%s tilted=%s lowered=%s conf=%.2f frames=%d %dms",
                 v.drank, v.lifted, v.at_mouth, v.tilted, v.lowered, v.confidence, len(frames), v.ms)
        return v
    except Exception as e:  # noqa: BLE001
        log.warning("frame check failed: %s after %dms", type(e).__name__, int((time.monotonic() - t0) * 1000))
        return None


def combine(confidence: float, evidence: str, v: Verdict | None) -> tuple[float, str, str]:
    """The live model's confidence, adjusted by the frame check. Returns (confidence, evidence, why).
    - The frame check saw lift-to-mouth AND tilt: raise to at least 0.8 (it is a real observation).
    - It is sure there was no drink although the cup was at her mouth (a held mug): cap at 0.45, i.e. "not sure, ask again",
      only when HEARTH_VERIFY_VETO=1 (off by default: a wrong "no" must not un-count a real sip).
    - No verdict, or nothing decisive: unchanged."""
    if v is None:
        return confidence, evidence, "none"
    if v.drank and v.confidence >= 0.6:
        note = " · frame check: cup to mouth and tilted" + (", then lowered" if v.lowered else "")
        return max(confidence, 0.8), (evidence + note)[:240], "raised" if confidence < 0.8 else "agreed"
    veto = os.environ.get("HEARTH_VERIFY_VETO", "0").strip() not in {"0", "false", "off"}  # off: the check may raise, never lower
    if veto and not v.drank and v.at_mouth and not v.tilted and v.confidence >= 0.6:
        return min(confidence, 0.45), evidence, "held_not_tilted"
    return confidence, evidence, "undecided"
