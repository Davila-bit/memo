"""The cue card: the agent's whole "screen" on her iPad, rendered with Pillow, sent as a camera track.

She never touches anything, so this is the entire UI. Everything that matters stays inside the
central 4:3 area (FaceTime "fill" framing crops the sides of a 16:9 picture). Frames live in memory
only; nothing is ever written to disk.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps

log = logging.getLogger("hearth.cuecard")

W, H = 1280, 720
SAFE_X0, SAFE_X1 = 160, 1120  # central 4:3 (960 x 720)
SAFE_W = SAFE_X1 - SAFE_X0

PAPER = (0xF6, 0xF1, 0xE9)
INK = (0x1F, 0x2A, 0x2E)
SAGE = (0x5E, 0x8C, 0x7B)
SAGE_DEEP = (0x3F, 0x66, 0x58)  # white text on this passes AA
AMBER = (0xB9, 0x77, 0x1F)
AMBER_SOFT = (0xF3, 0xE0, 0xC0)
CORAL_DEEP = (0xA8, 0x3A, 0x2F)  # white text on this passes AA
MUTED = (0x5A, 0x63, 0x66)
WHITE = (0xFF, 0xFF, 0xFF)

Mode = Literal["waiting", "asking", "watching", "thanks", "okay", "concern"]
Kind = Literal["drink", "meal", "med"]

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        log.warning("font %s missing; using Pillow default", name)
        return ImageFont.load_default(size)


@dataclass(frozen=True)
class CardState:
    mode: Mode = "waiting"
    kind: Kind = "drink"
    name: str = "Margaret"
    helper_label: str = "Sarah's helper"
    line: str = ""
    caption: str = ""
    slot_label: str | None = None
    photo: Image.Image | None = None  # already decoded, in memory


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _fit_lines(draw, text: str, font_name: str, sizes: tuple[int, ...], max_w: int, max_lines: int):
    """Largest size at which the text fits in max_lines without truncation (else the smallest)."""
    for size in sizes:
        f = _font(font_name, size)
        lines = _wrap(draw, text, f, max_w, 99)
        if len(lines) <= max_lines:
            return f, lines, size
    f = _font(font_name, sizes[-1])
    return f, _wrap(draw, text, f, max_w, max_lines), sizes[-1]


def _tail_lines(draw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    """The LAST max_lines of wrapped text: the caption shows the newest words."""
    lines = _wrap(draw, text, font, max_w, 99)
    return lines[-max_lines:]


def _draw_check(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color) -> None:
    pts = [(cx - r * 0.55, cy + r * 0.02), (cx - r * 0.15, cy + r * 0.42), (cx + r * 0.6, cy - r * 0.45)]
    draw.line(pts, fill=color, width=max(6, r // 5), joint="curve")


def _draw_eye(draw: ImageDraw.ImageDraw, x: int, y: int, s: int, color) -> None:
    draw.ellipse((x, y + s * 0.2, x + s * 1.6, y + s * 0.8 + s * 0.2), outline=color, width=4)
    draw.ellipse((x + s * 0.55, y + s * 0.25 + s * 0.05, x + s * 1.05, y + s * 0.75 + s * 0.15), fill=color)


def _illustration(kind: Kind, size: int) -> Image.Image:
    """A simple friendly picture when the family hasn't uploaded a photo yet."""
    img = Image.new("RGB", (size, size), (0xEC, 0xE4, 0xD6))
    d = ImageDraw.Draw(img)
    s = size
    if kind == "drink":  # a mug with steam
        d.rounded_rectangle((s * 0.22, s * 0.32, s * 0.68, s * 0.82), radius=int(s * 0.06), fill=(0x4A, 0x6F, 0xA5))
        d.ellipse((s * 0.6, s * 0.42, s * 0.86, s * 0.68), outline=(0x4A, 0x6F, 0xA5), width=int(s * 0.05))
        for i in range(3):
            x = s * (0.32 + i * 0.12)
            d.arc((x, s * 0.1, x + s * 0.08, s * 0.28), 90, 270, fill=MUTED, width=int(s * 0.015) + 2)
    elif kind == "meal":  # a plate with food
        d.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=WHITE, outline=(0xC9, 0xBF, 0xAE), width=6)
        d.ellipse((s * 0.25, s * 0.25, s * 0.75, s * 0.75), outline=(0xE2, 0xDA, 0xCB), width=4)
        d.ellipse((s * 0.32, s * 0.36, s * 0.52, s * 0.56), fill=(0xD9, 0x9A, 0x3E))
        d.ellipse((s * 0.5, s * 0.45, s * 0.66, s * 0.61), fill=SAGE)
        d.rectangle((s * 0.36, s * 0.56, s * 0.6, s * 0.64), fill=(0xB5, 0x5D, 0x3C))
    else:  # a pill organiser
        d.rounded_rectangle((s * 0.08, s * 0.3, s * 0.92, s * 0.7), radius=int(s * 0.05), fill=(0x8C, 0x7A, 0xB8))
        cell = (s * 0.84) / 4
        for i in range(4):
            x0 = s * 0.08 + i * cell + 8
            d.rounded_rectangle((x0, s * 0.34, x0 + cell - 16, s * 0.66), radius=10, fill=(0xB9, 0xAD, 0xD6))
        d.ellipse((s * 0.2, s * 0.45, s * 0.28, s * 0.53), fill=WHITE)
    return img


def _rounded_paste(base: Image.Image, img: Image.Image, box: tuple[int, int, int, int], radius: int) -> None:
    w, h = box[2] - box[0], box[3] - box[1]
    fitted = ImageOps.fit(img.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    base.paste(fitted, box[:2], mask)


def render(state: CardState) -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    # --- top: who is calling, and the watching indicator
    label_f = _font("Inter-SemiBold.ttf", 30)
    d.text((SAFE_X0 + 8, 22), state.helper_label, font=label_f, fill=MUTED)
    if state.mode == "watching":
        wf = _font("Inter-SemiBold.ttf", 30)
        text = "I'm watching"
        tw = d.textlength(text, font=wf)
        x = SAFE_X1 - 8 - tw
        d.rounded_rectangle((x - 70, 14, SAFE_X1, 66), radius=26, fill=AMBER_SOFT)
        _draw_eye(d, int(x - 58), 24, 28, INK)
        d.text((x - 6, 22), text, font=wf, fill=INK)

    caption_top = 586
    # --- middle
    if state.mode == "waiting":
        f, lines, size = _fit_lines(d, state.line or f"{state.helper_label} is calling",
                                    "Fraunces-SemiBold.ttf", (84, 76, 72), SAFE_W - 40, 2)
        block_h = len(lines) * int(size * 1.18)
        y = (caption_top - block_h) // 2 + 10
        for ln in lines:
            tw = d.textlength(ln, font=f)
            d.text(((W - tw) / 2, y), ln, font=f, fill=INK)
            y += int(size * 1.18)
    elif state.mode in ("thanks", "concern", "okay"):
        if state.mode == "thanks":
            band, fg, text = SAGE_DEEP, WHITE, f"Thank you, {state.name}"
        elif state.mode == "concern":
            band, fg, text = CORAL_DEEP, WHITE, state.line or "Sarah is coming. Stay where you are."
        else:
            band, fg, text = AMBER_SOFT, INK, state.line or "That's all right"
        d.rounded_rectangle((SAFE_X0, 110, SAFE_X1, caption_top - 30), radius=36, fill=band)
        f, lines, size = _fit_lines(d, text, "Fraunces-SemiBold.ttf", (84, 76, 72), SAFE_W - 120, 2)
        mark = 110 if state.mode == "thanks" else 0
        block_h = len(lines) * int(size * 1.18) + mark
        y = 110 + ((caption_top - 30 - 110) - block_h) // 2
        if state.mode == "thanks":
            d.ellipse((W / 2 - 48, y, W / 2 + 48, y + 96), fill=WHITE)
            _draw_check(d, W // 2, int(y + 48), 40, SAGE_DEEP)
            y += mark
        for ln in lines:
            tw = d.textlength(ln, font=f)
            d.text(((W - tw) / 2, y), ln, font=f, fill=fg)
            y += int(size * 1.18)
    else:  # asking / watching: her object large + one very large line
        photo_box = (SAFE_X0 + 20, 90, SAFE_X0 + 20 + 330, 90 + 330)
        photo = state.photo or _illustration(state.kind, 330)
        _rounded_paste(img, photo, photo_box, 28)
        tx0 = photo_box[2] + 36
        tw_max = SAFE_X1 - 10 - tx0
        f, lines, size = _fit_lines(d, state.line, "Fraunces-SemiBold.ttf", (80, 76, 72), tw_max, 3)
        y = 100
        if state.kind == "med" and state.slot_label:
            sf = _font("Inter-SemiBold.ttf", 52)
            sw = d.textlength(state.slot_label, font=sf)
            d.rounded_rectangle((tx0, y, tx0 + sw + 48, y + 84), radius=20, fill=AMBER)
            d.text((tx0 + 24, y + 12), state.slot_label, font=sf, fill=WHITE)
            y += 110
            f, lines, size = _fit_lines(d, state.line, "Fraunces-SemiBold.ttf", (76, 72), tw_max, 2)
        for ln in lines:
            d.text((tx0, y), ln, font=f, fill=INK)
            y += int(size * 1.15)

    # --- caption strip: the helper's current sentence, large, high contrast
    d.rectangle((0, caption_top, W, H), fill=INK)
    if state.caption:
        cf = _font("Inter-SemiBold.ttf", 44)
        lines = _tail_lines(d, state.caption, cf, SAFE_W - 20, 2)
        y = caption_top + (H - caption_top - len(lines) * 54) // 2
        for ln in lines:
            d.text((SAFE_X0 + 10, y), ln, font=cf, fill=PAPER)
            y += 54
    return img


def decode_photo(data: bytes) -> Image.Image | None:
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
        im.thumbnail((800, 800))
        return im.convert("RGB")
    except Exception:  # noqa: BLE001
        log.warning("cue photo could not be decoded; using the illustration")
        return None


class CueCard:
    """Owns the card state and the LiveKit video source; redraws only when the state changes."""

    def __init__(self, initial: CardState, fps: float = 8.0) -> None:
        self._state = initial
        self._fps = fps
        self._frame_bytes: bytes | None = None
        self._dirty = True
        self._task: asyncio.Task | None = None
        self.source = None
        self.publication = None

    @property
    def state(self) -> CardState:
        return self._state

    def update(self, **changes) -> None:
        new = replace(self._state, **changes)
        if new != self._state:
            self._state = new
            self._dirty = True

    def frame_rgba(self) -> bytes:
        if self._dirty or self._frame_bytes is None:
            t0 = time.perf_counter()
            st = self._state
            self._dirty = False
            self._frame_bytes = render(st).convert("RGBA").tobytes()
            if self._state is not st:
                self._dirty = True  # changed while drawing; redraw next tick
            log.debug("cue card redraw mode=%s in %.0f ms", st.mode, (time.perf_counter() - t0) * 1000)
        return self._frame_bytes

    async def publish(self, room) -> None:
        from livekit import rtc

        self.source = rtc.VideoSource(W, H)
        track = rtc.LocalVideoTrack.create_video_track("hearth-cue-card", self.source)
        opts = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA,
            simulcast=False,
            video_encoding=rtc.VideoEncoding(max_framerate=10, max_bitrate=2_000_000),
            degradation_preference=rtc.DegradationPreference.MAINTAIN_RESOLUTION,
        )
        self._push_one()  # a frame is ready before anyone subscribes
        self.publication = await room.local_participant.publish_track(track, opts)
        self._task = asyncio.create_task(self._loop(), name="cue-card")

    def _push_one(self) -> None:
        from livekit import rtc

        frame = rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, self.frame_rgba())
        self.source.capture_frame(frame)

    async def _loop(self) -> None:
        period = 1.0 / self._fps
        while True:
            try:
                await asyncio.to_thread(self.frame_rgba) if self._dirty else None
                self._push_one()
            except Exception:  # noqa: BLE001
                log.exception("cue card frame failed")
            await asyncio.sleep(period)

    async def aclose(self) -> None:
        if self._task:
            self._task.cancel()
