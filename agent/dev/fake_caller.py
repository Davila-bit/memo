"""A fake "her" for agent tests: joins as kiosk-test, publishes a camera track (a still image or
generated frames, or none) and a microphone track that plays WAV lines.

Each line is played after the helper finishes an utterance (simple energy VAD on the helper's
audio), so the conversation takes turns like a real one. Nothing is recorded.

    uv run python dev/fake_caller.py --room agent-test-x --image dev/fixtures/kitchen_mug.jpg \
        --lines refuse1,refuse2
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
import wave
from datetime import timedelta
from pathlib import Path

import numpy as np
from livekit import api, rtc
from PIL import Image, ImageDraw

log = logging.getLogger("fake-caller")
AUDIO_DIR = Path(__file__).parent / "audio"
SR = 24000


def token(room: str, identity: str) -> str:
    return (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_name("Test caller")
        .with_ttl(timedelta(minutes=30))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )


def load_wav(name: str) -> np.ndarray:
    with wave.open(str(AUDIO_DIR / f"{name}.wav"), "rb") as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1, "mono s16 expected"
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        rate = w.getframerate()
    if rate != SR:
        idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * SR / rate)).astype(np.int64)
        pcm = pcm[idx]
    # trim leading/trailing silence so turns are snappy
    loud = np.where(np.abs(pcm) > 600)[0]
    if len(loud):
        pcm = pcm[max(0, loud[0] - 2400): loud[-1] + 4800]
    return pcm


def make_frame_image(image: str | None) -> Image.Image:
    if image:
        return Image.open(image).convert("RGB").resize((640, 480))
    im = Image.new("RGB", (640, 480), (200, 190, 175))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 330, 640, 480), fill=(150, 120, 90))  # table
    d.ellipse((250, 90, 390, 230), fill=(225, 190, 160))  # a head
    d.rectangle((230, 230, 410, 380), fill=(90, 110, 150))  # shoulders
    return im


class Caller:
    def __init__(self, room_name: str, image: str | None, video: bool, lines: list[str],
                 identity: str = "kiosk-test") -> None:
        self.room_name = room_name
        self.image = image
        self.video = video
        self.lines = [load_wav(n) for n in lines]
        self.names = lines
        self.identity = identity
        self.room = rtc.Room()
        self.audio = rtc.AudioSource(SR, 1)
        self.helper_spoke_at: float | None = None
        self.helper_quiet = asyncio.Event()
        self._speaking = False
        self._last_loud = 0.0
        self.first_helper_audio_at: float | None = None

    async def run(self, duration: float) -> None:
        url = os.environ["LIVEKIT_URL"]
        self.room.on("track_subscribed", self._on_track)
        await self.room.connect(url, token(self.room_name, self.identity))
        log.info("caller joined %s", self.room_name)
        mic = rtc.LocalAudioTrack.create_audio_track("mic", self.audio)
        await self.room.local_participant.publish_track(
            mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        tasks = [asyncio.create_task(self._guard(self._audio_loop()))]
        if self.video:
            src = rtc.VideoSource(640, 480)
            cam = rtc.LocalVideoTrack.create_video_track("camera", src)
            await self.room.local_participant.publish_track(
                cam, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
            tasks.append(asyncio.create_task(self._video_loop(src)))
        try:
            await asyncio.sleep(duration)
        finally:
            for t in tasks:
                t.cancel()
            await self.room.disconnect()

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("caller task failed")

    async def _video_loop(self, src: rtc.VideoSource) -> None:
        data = make_frame_image(self.image).convert("RGBA").tobytes()
        frame = rtc.VideoFrame(640, 480, rtc.VideoBufferType.RGBA, data)
        while True:
            src.capture_frame(frame)
            await asyncio.sleep(0.2)

    def _on_track(self, track: rtc.Track, pub, participant: rtc.RemoteParticipant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity.startswith("agent"):
            log.info("subscribed to helper audio")
            asyncio.create_task(self._listen(track))

    async def _listen(self, track: rtc.Track) -> None:
        stream = rtc.AudioStream(track, sample_rate=SR, num_channels=1)
        async for ev in stream:
            x = np.frombuffer(ev.frame.data, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
            now = time.monotonic()
            if rms > 300:
                if self.first_helper_audio_at is None:
                    self.first_helper_audio_at = now
                    log.info("helper audio heard")
                self._last_loud = now
                if not self._speaking:
                    self._speaking = True
                    self.helper_quiet.clear()
            elif self._speaking and now - self._last_loud > 1.2:
                self._speaking = False
                log.info("helper finished an utterance")
                self.helper_quiet.set()

    async def _audio_loop(self) -> None:
        chunk = SR // 100  # 10 ms
        silence = np.zeros(chunk, dtype=np.int16)
        queue = list(zip(self.names, self.lines))

        async def push(samples: np.ndarray) -> None:
            for i in range(0, len(samples), chunk):
                part = samples[i:i + chunk]
                if len(part) < chunk:
                    part = np.pad(part, (0, chunk - len(part)))
                await self.audio.capture_frame(rtc.AudioFrame(part.tobytes(), SR, 1, chunk))

        # one writer only: silence until the helper finishes an utterance, then the next line
        while True:
            if queue and self.helper_quiet.is_set():
                self.helper_quiet.clear()
                name, pcm = queue.pop(0)
                await push(np.zeros(SR // 2, dtype=np.int16))
                log.info("caller says line %s", name)
                await push(pcm)
            else:
                await push(silence)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", required=True)
    ap.add_argument("--image")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--lines", default="")
    ap.add_argument("--duration", type=float, default=120)
    a = ap.parse_args()
    c = Caller(a.room, a.image, not a.no_video, [x for x in a.lines.split(",") if x])
    asyncio.run(c.run(a.duration))


if __name__ == "__main__":
    main()
