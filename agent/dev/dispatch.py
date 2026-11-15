"""Create a test room, dispatch hearth-agent with an AgentJob, optionally run the fake caller in the
same process, then set the room's `hearth_live` metadata after N seconds. Deletes the room at the end.

    uv run python dev/dispatch.py --scenario refusal
    uv run python dev/dispatch.py --live-after 5 --no-caller --hold 300   # join from a browser yourself

Prints: the room name, when the agent's audio + video tracks appear (the bridge's dial condition),
live -> first helper audio latency, and the stub API log.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import uuid

import httpx
from livekit import api

from hearth_contracts import AgentJob, Consent, Cue, Resident

log = logging.getLogger("dispatch")
HERE = os.path.dirname(__file__)

SCENARIOS: dict[str, dict] = {
    # her lines are played one per helper utterance
    "refusal": {"lines": ["refuse1", "refuse2", "refuse2"], "video": True, "image": None},
    "concern": {"lines": ["fell"], "video": True, "image": None},
    "novideo": {"lines": ["cup", "drinking", "cup"], "video": False, "image": None},
    "still": {"lines": ["cup", "drinking", "cup", "drinking"], "video": True,
              "image": os.path.join(HERE, "fixtures", "kitchen_mug.jpg")},
    # auto-answered, nobody there: silence + a static frame -> no_answer at ~40 s
    "empty": {"lines": [], "video": True, "image": None},
    "budget": {"lines": ["chat", "hello", "chat", "hello", "chat", "hello", "chat"], "video": True, "image": None},
}


def make_job(checkin_id: str, kind: str, api_base: str, max_seconds: int, prefix: str) -> AgentJob:
    resident = Resident(
        id="margaret", name="Margaret Ellis", preferred_name="Margaret", family_name_for_her="Mum",
        daughter_name="Sarah", consent=Consent(status="signed", signed_by="Margaret Ellis", relation="self"),
    )
    cue = {
        "drink": Cue(id="cue-mug", kind="drink", title="Your mug", line="Time for some water, Margaret"),
        "meal": Cue(id="cue-plate", kind="meal", title="Your lunch plate", line="Lunch time, Margaret"),
        "med": Cue(id="cue-pills", kind="med", title="Your pill box", line="Your morning tablets",
                   slot_label="TUE · MORNING"),
    }[kind]
    return AgentJob(checkin_id=checkin_id, kind=kind, transport="kiosk", resident=resident, cue=cue,  # type: ignore[arg-type]
                    api_base=api_base, caller_identity_prefix=prefix, max_seconds=max_seconds,
                    context="She has had 2 drinks today (about 320 ml).")


async def watch_agent_tracks(lk: api.LiveKitAPI, room: str, t0: float, stop: asyncio.Event) -> None:
    seen = False
    while not stop.is_set() and not seen:
        try:
            res = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
            for p in res.participants:
                if p.kind == api.ParticipantInfo.Kind.AGENT or p.identity.startswith("agent"):
                    audio = [t for t in p.tracks if t.type == 0]
                    video = [t for t in p.tracks if t.type == 1]
                    unmuted = [t for t in audio if not t.muted]
                    if audio or video:
                        log.info("agent tracks at %.1fs: audio=%d (unmuted %d) video=%d", time.monotonic() - t0,
                                 len(audio), len(unmuted), len(video))
                    if len(unmuted) == 1 and len(video) >= 1:
                        log.info("DIAL CONDITION MET at %.1fs (one unmuted audio + video)", time.monotonic() - t0)
                        seen = True
        except Exception as e:  # noqa: BLE001
            log.warning("list_participants: %s", type(e).__name__)
        await asyncio.sleep(0.5)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=list(SCENARIOS), default=None)
    ap.add_argument("--kind", default="drink", choices=["drink", "meal", "med"])
    ap.add_argument("--live-after", type=float, default=6.0)
    ap.add_argument("--max-seconds", type=int, default=90)
    ap.add_argument("--hold", type=float, default=120, help="seconds to keep the room after live")
    ap.add_argument("--no-caller", action="store_true")
    ap.add_argument("--api-base", default="http://127.0.0.1:8103")
    ap.add_argument("--prefix", default="kiosk-")
    ap.add_argument("--agent-name", default=os.environ.get("HEARTH_AGENT_NAME", "hearth-agent"))
    a = ap.parse_args()

    cid = "t" + uuid.uuid4().hex[:8]
    room = f"agent-test-{cid}"
    job = make_job(cid, a.kind, a.api_base, a.max_seconds, a.prefix)
    lk = api.LiveKitAPI()
    t0 = time.monotonic()
    stop = asyncio.Event()
    caller_task = None
    try:
        await lk.room.create_room(api.CreateRoomRequest(name=room, empty_timeout=60))
        log.info("room %s created", room)
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=a.agent_name, room=room, metadata=job.model_dump_json()))
        log.info("dispatched %s (checkin %s, kind %s, max %ss)", a.agent_name, cid, a.kind, a.max_seconds)
        watcher = asyncio.create_task(watch_agent_tracks(lk, room, t0, stop))

        caller = None
        if a.scenario and not a.no_caller:
            from fake_caller import Caller  # noqa: PLC0415

            sc = SCENARIOS[a.scenario]
            caller = Caller(room, sc["image"], sc["video"], sc["lines"])
            caller_task = asyncio.create_task(caller.run(a.live_after + a.hold))
        else:
            from urllib.parse import urlencode  # noqa: PLC0415

            from fake_caller import token  # noqa: PLC0415

            tok = token(room, f"{a.prefix}browser")
            q = urlencode({"liveKitUrl": os.environ["LIVEKIT_URL"], "token": tok})
            # A short-lived join token for this one test room only (not an API key).
            print(f"\nOpen in a browser, allow camera + mic, join BEFORE live ({a.live_after:.0f}s):\n"
                  f"  https://meet.livekit.io/custom?{q}\n", flush=True)

        await asyncio.sleep(a.live_after)
        live_at = time.monotonic()
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(
            room=room, metadata=json.dumps({"hearth_live": True, "checkin_id": cid})))
        log.info("LIVE set at %.1fs", live_at - t0)

        # run until the agent posts `end` (or hold expires)
        deadline = live_at + a.hold
        async with httpx.AsyncClient(timeout=3) as c:
            while time.monotonic() < deadline:
                if caller and caller.first_helper_audio_at and not getattr(caller, "_lat", False):
                    caller._lat = True  # type: ignore[attr-defined]
                    log.info("LATENCY live -> first helper audio heard by caller: %.2fs",
                             caller.first_helper_audio_at - live_at)
                try:
                    entries = (await c.get(f"{a.api_base}/stub/log")).json()
                    mine = [e for e in entries if e["checkin"] == cid]
                    if any(e["call"] == "end" for e in mine):
                        log.info("agent posted end at %.1fs after live", time.monotonic() - live_at)
                        await asyncio.sleep(5)
                        break
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.5)
            entries = (await c.get(f"{a.api_base}/stub/log")).json()
        print("\n=== stub log for", cid, "===")
        for e in entries:
            if e["checkin"] == cid:
                print("STUBLOG", json.dumps(e))
        stop.set()
        watcher.cancel()
    finally:
        if caller_task:
            caller_task.cancel()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room))
            log.info("room %s deleted", room)
        except Exception as e:  # noqa: BLE001
            log.warning("delete_room: %s", type(e).__name__)
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
