"""Live check of hearth.calls against LiveKit Cloud only. Places NO FaceTime call.

Run inside the workspace (keys via fnox; names only):
  fnox exec --only LIVEKIT_URL,LIVEKIT_API_KEY,LIVEKIT_API_SECRET -- \
    env WS_ENV=LIVEKIT_URL,LIVEKIT_API_KEY,LIVEKIT_API_SECRET scripts/ws \
    'cd backend && uv run --with playwright --with livekit python tests/calls/live_livekit.py'

A. FaceTime path with no agent running: room created, dispatch sent, "agent did not join", room deleted.
B. Kiosk page served on :8102 in headless Chromium (fake camera/mic) joins kiosk-<device> and publishes
   muted audio + video.
C. Kiosk check-in with a stand-in agent (this script joins as `agent-calls-fake` with a silent audio track
   and a plain video track): ringing -> the page auto-answers -> connected -> end -> agent removed,
   kiosk still present and muted again.
Nothing from the media is written to disk.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import logging
import sys
import time
from typing import Any

import uvicorn
from fastapi import FastAPI
from hearth_contracts.models import AgentJob, Checkin, Consent, Resident
from livekit import api, rtc

from hearth.calls import CallSettings, HearthCallService, Timings, router
from hearth.calls.livekit_ops import LiveKitRoomOps, mint_token

PORT = 8102
DEVICE = "calls-test"
KIOSK = f"kiosk-{DEVICE}"
NO_AGENT = "calls-test-noagent"  # no worker registers this name
T0 = time.time()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
FAIL: list[str] = []


def say(*a: object) -> None:
    print(f"[{time.time() - T0:5.1f}s]", *a, flush=True)


def check(ok: bool, what: str) -> None:
    say(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAIL.append(what)


class NoDialChert:
    """Line looks available; any attempt to place a call is a bug in this test."""

    def __init__(self, line_id: str) -> None:
        self.line_id = line_id

    async def list_lines(self) -> list[dict[str, Any]]:
        return [{"id": self.line_id, "status": "available", "availability": "available"}]

    async def create_call(self, body: dict[str, Any], key: str) -> Any:
        raise AssertionError("this test must never place a FaceTime call")

    async def get_call(self, call_id: str) -> dict[str, Any]:
        raise AssertionError("no call")

    async def get_events(self, call_id: str) -> list[dict[str, Any]]:
        return []

    async def end_call(self, call_id: str, key: str) -> int:
        raise AssertionError("no call")

    async def aclose(self) -> None:
        pass


class Cb:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any, Any]] = []
        self.done = asyncio.Event()
        self.connected = asyncio.Event()

    async def call_ringing(self, cid: str) -> None:
        say("cb: ringing")
        self.events.append(("ringing", None, None))

    async def call_connected(self, cid: str) -> None:
        say("cb: connected")
        self.events.append(("connected", None, None))
        self.connected.set()

    async def call_finished(self, cid: str, outcome: Any, error: Any) -> None:
        say(f"cb: finished outcome={outcome} error={error}")
        self.events.append(("finished", outcome, error))
        self.done.set()


def mk(transport: str, cid: str, room: str) -> tuple[Checkin, AgentJob]:
    r = Resident(id="margaret", name="Margaret Ellis", preferred_name="Margaret", family_name_for_her="Mum",
                 daughter_name="Sarah", consent=Consent(status="signed", signed_by="Margaret Ellis", relation="self"))
    c = Checkin(id=cid, resident_id="margaret", kind="drink", transport=transport, status="queued",  # type: ignore[arg-type]
                room=room, created_at=dt.datetime.now(dt.UTC))
    j = AgentJob(checkin_id=cid, kind="drink", transport=transport, resident=r, api_base="http://127.0.0.1:8000",  # type: ignore[arg-type]
                 caller_identity_prefix="chert-bridge-" if transport == "facetime" else "kiosk-", max_seconds=30)
    return c, j


async def room_exists(lk: api.LiveKitAPI, name: str) -> bool:
    res = await lk.room.list_rooms(api.ListRoomsRequest(names=[name]))
    return any(r.name == name for r in res.rooms)


async def kiosk_tracks(rooms: LiveKitRoomOps) -> list[tuple[str, bool]] | None:
    for p in await rooms.participants(KIOSK):
        if p.identity == KIOSK:
            return [(t.kind, t.muted) for t in p.tracks]
    return None


async def fake_agent(s: CallSettings, stop: asyncio.Event) -> None:
    """Stand-in for hearth-agent: one unmuted (silent) audio track + one video track. In memory only."""
    assert s.livekit_api_key and s.livekit_api_secret and s.livekit_url
    room = rtc.Room()
    tok = mint_token(s.livekit_api_key, s.livekit_api_secret, identity="agent-calls-fake", room=KIOSK, ttl_seconds=600)
    await room.connect(s.livekit_url, tok)
    asrc, vsrc = rtc.AudioSource(48000, 1), rtc.VideoSource(640, 360)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("voice", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    await room.local_participant.publish_track(
        rtc.LocalVideoTrack.create_video_track("cue-card", vsrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    say("fake agent joined and published audio + video")
    frame = bytes([246, 241, 233, 255]) * (640 * 360)  # paper colour
    silence = bytes(480 * 2)
    try:
        while not stop.is_set():
            vsrc.capture_frame(rtc.VideoFrame(640, 360, rtc.VideoBufferType.RGBA, frame))
            for _ in range(20):
                await asrc.capture_frame(rtc.AudioFrame(silence, 48000, 1, 480))
    finally:
        await room.disconnect()


async def main() -> int:
    from playwright.async_api import async_playwright

    base = CallSettings.from_env()
    if not base.livekit_ready:
        print("LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
        return 2
    s = dataclasses.replace(base, kiosk_device=DEVICE, agent_name=NO_AGENT, chert_line_id="line-test",
                            chert_ipad_number="4045550123", skip_line_check=False)
    assert s.livekit_url and s.livekit_api_key and s.livekit_api_secret
    rooms = LiveKitRoomOps(s.livekit_url, s.livekit_api_key, s.livekit_api_secret)
    svc = HearthCallService(s, rooms=rooms, chert=NoDialChert("line-test"),
                            timings=Timings(agent_join=8, kiosk_presence=10, kiosk_answer=12))
    lk = api.LiveKitAPI(s.livekit_url, s.livekit_api_key, s.livekit_api_secret)

    # ---- A: facetime, no agent running
    rid = f"calls-a{int(time.time())}"
    c, j = mk("facetime", rid, rid)
    cb = Cb()
    await svc.start(c, j, cb)
    await asyncio.sleep(2)
    check(await room_exists(lk, rid), f"A: room {rid} was created")
    await asyncio.wait_for(cb.done.wait(), 30)
    check(cb.events == [("finished", "error", "agent did not join")], "A: finished once with 'agent did not join'")
    await asyncio.sleep(1)
    check(not await room_exists(lk, rid), f"A: room {rid} deleted after the failed join")

    # ---- B: kiosk page in headless Chromium
    app = FastAPI()
    app.include_router(router)

    @app.get("/api/kiosk/token")
    async def kiosk_token(device: str = "kitchen", key: str = "") -> dict[str, str]:  # stub of the core route
        return (await svc.kiosk_token(device)).model_dump()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    srv = asyncio.create_task(server.serve())
    await asyncio.sleep(1.5)
    stop_agent = asyncio.Event()
    agent_task: asyncio.Task[None] | None = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=[
                "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required"])
            ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
            await ctx.grant_permissions(["camera", "microphone"], origin=f"http://127.0.0.1:{PORT}")
            page = await ctx.new_page()
            page.on("console", lambda m: say(f"page console {m.type}: {m.text[:160]}") if m.type in ("error", "warning") else None)
            await page.goto(f"http://127.0.0.1:{PORT}/kiosk?key=test&device={DEVICE}")
            await page.get_by_role("button", name="Tap once to get ready").click()
            tracks = None
            for _ in range(40):
                await asyncio.sleep(0.5)
                tracks = await kiosk_tracks(rooms)
                if tracks and {k for k, _ in tracks} >= {"audio", "video"}:
                    break
            say(f"kiosk participant tracks (kind, muted): {tracks}")
            check(bool(tracks) and {k for k, _ in tracks or []} >= {"audio", "video"},
                  "B: kiosk participant is in list_participants with audio + video")
            check(bool(tracks) and all(m for _, m in tracks or []), "B: kiosk tracks are muted while idle")
            check(await page.locator("#idle").is_visible(), "B: page shows the idle clock")
            chip = await page.locator("#chipText").inner_text()
            check(chip == "Ready for calls", f"B: status chip says {chip!r}")

            # ---- C: kiosk check-in with a stand-in agent
            cid = f"calls-c{int(time.time())}"
            c2, j2 = mk("kiosk", cid, f"checkin-{cid}")
            cb2 = Cb()
            await svc.start(c2, j2, cb2)
            await asyncio.sleep(0.5)
            agent_task = asyncio.create_task(fake_agent(s, stop_agent))
            await page.locator("#ringing").wait_for(state="visible", timeout=15000)
            check(True, "C: page shows the ringing screen when the agent joins")
            await asyncio.wait_for(cb2.connected.wait(), 20)
            check(await page.locator("#call").is_visible(), "C: page auto-answered into the call view")
            tracks = await kiosk_tracks(rooms)
            say(f"kiosk tracks after answer: {tracks}")
            check(bool(tracks) and any(k == "audio" and not m for k, m in tracks or []), "C: kiosk mic unmuted on answer")
            await asyncio.sleep(2)
            await svc.end(cid, "done")
            await asyncio.wait_for(cb2.done.wait(), 30)
            check([e[0] for e in cb2.events] == ["ringing", "connected", "finished"], f"C: callbacks {cb2.events}")
            await page.locator("#idle").wait_for(state="visible", timeout=15000)
            check(True, "C: page went back to idle when the agent was removed")
            await asyncio.sleep(1.5)
            parts = [p.identity for p in await rooms.participants(KIOSK)]
            check(KIOSK in parts and "agent-calls-fake" not in parts, f"C: agent removed, kiosk kept: {parts}")
            tracks = await kiosk_tracks(rooms)
            check(bool(tracks) and all(m for _, m in tracks or []), f"C: kiosk muted again: {tracks}")
            await browser.close()
    finally:
        stop_agent.set()
        if agent_task:
            await asyncio.gather(agent_task, return_exceptions=True)
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=KIOSK))
        except Exception as e:  # noqa: BLE001
            say(f"delete {KIOSK}: {type(e).__name__}")
        server.should_exit = True
        await srv
        await svc.aclose()
        await lk.aclose()
    say("RESULT:", "all passed" if not FAIL else f"{len(FAIL)} failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
