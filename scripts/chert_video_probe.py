"""Chert FaceTime video gate probe.

Proves, on one short real call: (1) outbound FaceTime *video* connects, (2) our agent's video
(a test cue card) is published for the bridge, (3) the caller's camera reaches us as video frames
(`facetime-remote-video`), and (4) Gemini can describe one of those frames.

Privacy rule: frames stay in memory. Nothing from the call is written to disk.

Env: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and unless DRY=1 also CHERT_API_KEY,
CHERT_PROJECT_ID, CHERT_LINE_ID, DEST (E.164). Optional: GEMINI_API_KEY, CALL_SECONDS (default 35),
DTYPE (phone|email).
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import struct
import time
import urllib.error
import urllib.request

import numpy as np
from livekit import api, rtc
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 10
AGENT_ID = "hearth-agent"
CHERT = "https://facetime.trychert.com/api/v1"
DRY = os.environ.get("DRY") == "1"
CALL_SECONDS = int(os.environ.get("CALL_SECONDS", "35"))
T0 = time.time()


def log(*a: object) -> None:
    print(f"[{time.time() - T0:6.1f}s]", *a, flush=True)


def font(size: int) -> ImageFont.ImageFont:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


F_BIG, F_SMALL = font(110), font(44)


def cue_card(n: int) -> bytes:
    """Warm paper card, big text, a moving dot and a counter so motion is obvious on the iPad."""
    img = Image.new("RGBA", (W, H), "#F6F1E9")
    d = ImageDraw.Draw(img)
    d.text((W // 2, 250), "Hello from Hearth", font=F_BIG, fill="#1F2A2E", anchor="mm")
    d.text((W // 2, 400), "video test: can you see this card?", font=F_SMALL, fill="#5E8C7B", anchor="mm")
    d.text((W // 2, 500), f"frame {n}", font=F_SMALL, fill="#B9771F", anchor="mm")
    x = 140 + (n * 24) % (W - 280)
    d.ellipse((x - 30, 600, x + 30, 660), fill="#C8483B")
    return img.tobytes()


def chert(method: str, path: str, body: dict | None = None, idem: str | None = None) -> tuple[int, dict]:
    headers = {
        "Authorization": f"Bearer {os.environ['CHERT_API_KEY']}",
        "X-Chert-Project-Id": os.environ["CHERT_PROJECT_ID"],
        "Content-Type": "application/json",
        "User-Agent": "hearth-probe/0.1",
    }
    if idem:
        headers["Idempotency-Key"] = idem
    req = urllib.request.Request(
        CHERT + path, method=method, headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300].decode(errors="replace")}


def token(identity: str, room: str, ttl: int = 900) -> str:
    return (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity)
        .with_name(identity)
        .with_ttl(__import__("datetime").timedelta(seconds=ttl))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )


class Stats:
    def __init__(self) -> None:
        self.video: dict[str, dict] = {}
        self.audio: dict[str, int] = {}
        self.last_jpeg: bytes | None = None  # in memory only


async def watch_video(track: rtc.Track, key: str, st: Stats) -> None:
    s = st.video.setdefault(key, {"frames": 0, "size": None, "changed": 0, "first_at": None})
    prev = None
    async for ev in rtc.VideoStream(track):
        f = ev.frame
        s["frames"] += 1
        if s["first_at"] is None:
            s["first_at"] = round(time.time() - T0, 1)
            log(f"FIRST VIDEO FRAME from {key}: {f.width}x{f.height}")
        s["size"] = (f.width, f.height)
        if s["frames"] % 15 == 1:
            rgba = f.convert(rtc.VideoBufferType.RGBA)
            arr = np.frombuffer(rgba.data, dtype=np.uint8).reshape(rgba.height, rgba.width, 4)
            small = arr[::16, ::16, :3].astype(np.int16)
            if prev is not None and prev.shape == small.shape and np.abs(small - prev).mean() > 1.5:
                s["changed"] += 1
            prev = small
            s["mean_rgb"] = [int(x) for x in arr[..., :3].reshape(-1, 3).mean(axis=0)]
            buf = io.BytesIO()
            Image.fromarray(arr[..., :3]).save(buf, "JPEG", quality=80)
            st.last_jpeg = buf.getvalue()


async def watch_audio(track: rtc.Track, key: str, st: Stats) -> None:
    async for _ in rtc.AudioStream(track):
        st.audio[key] = st.audio.get(key, 0) + 1


async def publish_loops(room: rtc.Room) -> None:
    vsrc = rtc.VideoSource(W, H)
    vtrack = rtc.LocalVideoTrack.create_video_track("cue-card", vsrc)
    # one sharp layer: FaceTime is the only viewer, so no simulcast
    await room.local_participant.publish_track(
        vtrack,
        rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA,
            simulcast=False,
            video_encoding=rtc.VideoEncoding(max_bitrate=2_500_000, max_framerate=FPS),
        ),
    )
    asrc = rtc.AudioSource(48000, 1)
    atrack = rtc.LocalAudioTrack.create_audio_track("agent-voice", asrc)
    await room.local_participant.publish_track(
        atrack, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    log("published agent video (cue-card) + audio (soft tone)")

    async def video() -> None:
        n = 0
        while True:
            vsrc.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, cue_card(n)))
            n += 1
            await asyncio.sleep(1 / FPS)

    async def audio() -> None:
        # soft two-note chime repeating, so the call is never "silent"
        sr, n = 48000, 480
        t = 0
        while True:
            hz = 440 if (t // sr) % 2 == 0 else 554
            amp = 2500
            data = struct.pack(
                f"<{n}h", *(int(amp * math.sin(2 * math.pi * hz * (t + i) / sr)) for i in range(n))
            )
            t += n
            await asrc.capture_frame(rtc.AudioFrame(data, sr, 1, n))

    await asyncio.gather(video(), audio())


def describe(jpeg: bytes) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return "(no GEMINI_API_KEY; skipped)"
    try:
        from google import genai
        from google.genai import types

        # gemini-3.8-flash stalled with no bytes on the free tier (Sat 23:24); flash-lite answers in <1 s
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=20_000))
        r = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            contents=[
                types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                "Describe this video-call frame in two sentences. Say whether you can see a person, "
                "a cup or mug, and whether there are any on-screen call controls or overlays.",
            ],
        )
        return (r.text or "").strip()
    except Exception as e:  # degrade gracefully
        return f"(gemini error: {type(e).__name__}: {str(e)[:200]})"


async def main() -> None:
    room_name = f"probe-{int(time.time())}"
    st = Stats()
    room = rtc.Room()

    @room.on("track_subscribed")
    def _sub(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        key = f"{p.identity}/{pub.name or pub.sid}"
        log(f"track subscribed: {key} kind={'video' if track.kind == rtc.TrackKind.KIND_VIDEO else 'audio'}")
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            asyncio.create_task(watch_video(track, key, st))
        else:
            asyncio.create_task(watch_audio(track, key, st))

    @room.on("participant_connected")
    def _pc(p: rtc.RemoteParticipant) -> None:
        log(f"participant joined: {p.identity}")

    @room.on("participant_disconnected")
    def _pd(p: rtc.RemoteParticipant) -> None:
        log(f"participant left: {p.identity}")

    @room.on("track_published")
    def _tp(pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log(f"track published by {p.identity}: name={pub.name!r} kind={pub.kind} source={pub.source}")

    @room.on("room_metadata_changed")
    def _md(old: str, new: str) -> None:
        log(f"room metadata: {new[:200]!r}")

    await room.connect(os.environ["LIVEKIT_URL"], token(AGENT_ID, room_name))
    log(f"agent joined room {room_name}")
    pub_task = asyncio.create_task(publish_loops(room))
    await asyncio.sleep(3)

    if DRY:
        log("DRY=1: no call placed; self-check of Gemini on our own cue card")
        buf = io.BytesIO()
        Image.frombytes("RGBA", (W, H), cue_card(7)).convert("RGB").save(buf, "JPEG")
        log("gemini:", await asyncio.to_thread(describe, buf.getvalue()))
        await asyncio.sleep(2)
        pub_task.cancel()
        await room.disconnect()
        return

    bridge_id = f"chert-bridge-{room_name}"
    body = {
        "direction": "outbound",
        "mode": "customer_managed",
        "line_id": os.environ["CHERT_LINE_ID"],
        "destination": os.environ["DEST"],
        "destination_type": os.environ.get("DTYPE", "phone"),
        "media": {"audio": True, "video": True},
        "livekit_url": os.environ["LIVEKIT_URL"],
        "participant_token": token(bridge_id, room_name),
        "remote_participant_identity": AGENT_ID,
    }
    code, resp = await asyncio.to_thread(chert, "POST", "/calls", body, f"{room_name}-create")
    data = resp.get("data") or {}
    call_id = data.get("id")
    log(f"create call: HTTP {code} status={data.get('status')} error={resp.get('error')}")
    if not call_id:
        log("no call id; response keys:", list(resp.keys()), str(resp)[:300])
        pub_task.cancel()
        await room.disconnect()
        return

    active_at = None
    last = None
    seen_events: set = set()
    deadline = time.time() + 75  # ring budget
    try:
        while True:
            await asyncio.sleep(2)
            _, r = await asyncio.to_thread(chert, "GET", f"/calls/{call_id}")
            d = r.get("data") or {}
            status = d.get("status")
            if status != last:
                log(f"call status: {status} term={d.get('terminal_reason')}/{d.get('terminal_detail')}")
                last = status
            for e in d.get("facetime_call_events") or []:
                if e.get("id") not in seen_events:
                    seen_events.add(e.get("id"))
                    log(f"chert event: {e.get('event_type')} ({e.get('source')})")
            if status == "active" and active_at is None:
                active_at = time.time()
            if status in ("ended", "failed", "completed", "canceled", "cancelled", "declined", "no_answer"):
                break
            if active_at is None and time.time() > deadline:
                log("not answered within ring budget; ending")
                break
            if active_at and time.time() - active_at > CALL_SECONDS:
                log(f"{CALL_SECONDS}s of active call reached; ending")
                break
    finally:
        code, _ = await asyncio.to_thread(chert, "POST", f"/calls/{call_id}/end", {}, f"{room_name}-end")
        log(f"end call: HTTP {code}")

    await asyncio.sleep(2)
    _, ev = await asyncio.to_thread(chert, "GET", f"/calls/{call_id}/events")
    evs = ev.get("data") or []
    if isinstance(evs, dict):
        evs = evs.get("events") or evs.get("items") or []
    log("events:")
    for e in list(evs)[:30]:
        print("    ", e.get("type") or e.get("event"), e.get("created_at") or e.get("at"), flush=True)

    log("=== RESULT ===")
    log("remote video tracks:", json.dumps(st.video) if st.video else "NONE RECEIVED")
    log("remote audio tracks (frame counts):", st.audio or "NONE RECEIVED")
    if active_at:
        log(f"active call seconds (approx): {time.time() - active_at:.0f}")
    if st.last_jpeg:
        log("gemini on last caller frame:", await asyncio.to_thread(describe, st.last_jpeg))
    st.last_jpeg = None
    pub_task.cancel()
    await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
