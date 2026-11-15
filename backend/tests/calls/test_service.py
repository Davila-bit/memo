from __future__ import annotations

import asyncio
import json
import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hearth.calls import HearthCallService, router
from hearth.calls.config import CallSettings, mask_number, to_e164

from .fakes import FAST, SETTINGS, FakeChert, FakeRooms, RecordingCallbacks, checkin, ev, job

LIVE = {"status": "active", "facetime_call_events": [ev("allocated", "control_plane"), ev("dialing"),
                                                       ev("active"), ev("bridge_ready")]}


async def run(svc: HearthCallService, c, cb: RecordingCallbacks, max_seconds: int = 5, timeout: float = 8.0):
    await svc.start(c, job(c, max_seconds), cb)
    await asyncio.wait_for(cb.done.wait(), timeout)
    await asyncio.sleep(0.05)  # let the task drop its bookkeeping
    return cb


# ---------------------------------------------------------------- facetime


async def test_facetime_happy_path_rings_connects_finishes_once():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "allocated"}, {"status": "dialing"}, {"status": "active"}, LIVE], rooms=rooms)
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    c = checkin()

    async def agent_ends_it():
        asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(svc.end(c.id, "done")))

    cb = await run(svc, c, RecordingCallbacks(on_connected=agent_ends_it))
    assert cb.kinds() == ["ringing", "connected", "finished"]
    assert cb.finished() == [("finished", None, None)]
    body, key = chert.created[0]
    assert body["destination"] == "+14045550123" and body["destination_type"] == "phone"
    assert body["media"] == {"audio": True, "video": True} and body["mode"] == "customer_managed"
    assert body["remote_participant_identity"] == "agent-AJ_1"
    assert 8 <= len(key) <= 200 and key.endswith("-create")
    assert len(chert.ended) == 1 and chert.ended[0].endswith("-end") and chert.ended[0] != key
    assert json.loads(rooms.metadata["checkin-ck1"][0]) == {"hearth_live": True, "checkin_id": "ck1"}
    assert rooms.metadata["checkin-ck1"][-1] == "{}"
    assert json.loads(rooms.last_metadata)["checkin_id"] == "ck1"  # dispatch metadata = AgentJob
    assert ("delete_dispatch", "AD_1", "checkin-ck1") in rooms.log and ("delete_room", "checkin-ck1") in rooms.log
    assert svc._calls == {}


async def test_active_without_bridge_ready_is_not_live():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}, {"status": "active", "facetime_call_events": [ev("active")]}],
                      rooms=rooms)
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    assert "connected" not in cb.kinds()
    assert cb.finished() == [("finished", "no_answer", None)]


async def test_not_answered_within_ring_time():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}], rooms=rooms)
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    assert cb.kinds() == ["ringing", "finished"]
    assert cb.finished() == [("finished", "no_answer", None)]
    assert len(chert.ended) == 1  # we hung up the unanswered call
    assert ("delete_room", "checkin-ck1") in rooms.log


async def test_declined_terminal_status_is_no_answer():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}, {"status": "ended", "terminal_detail": "declined"}], rooms=rooms)
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    assert cb.finished() == [("finished", "no_answer", None)]
    assert chert.ended == []  # already terminal: no end request


async def test_failed_before_answer_is_error_with_reason():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "failed", "terminal_reason": "unknown", "terminal_detail": "worker_restarted"}],
                      rooms=rooms)
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    assert cb.finished() == [("finished", "error", "call failed (worker_restarted)")]


async def test_chert_4xx_on_create():
    rooms = FakeRooms()
    chert = FakeChert(create_status=422, create_body={"error": {"code": "invalid_token_grants",
                                                                "message": "bad grants"}})
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    (_, outcome, error), = cb.finished()
    assert outcome == "error" and "invalid_token_grants" in error and "422" in error
    assert "ringing" not in cb.kinds() and chert.ended == []
    assert ("delete_room", "checkin-ck1") in rooms.log


async def test_line_busy_refuses_before_any_room_or_call():
    rooms = FakeRooms()
    chert = FakeChert(lines=[{"id": "line-1", "status": "busy", "availability": "busy",
                              "current_call_id": "529f0346-aaaa"}])
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    (_, outcome, error), = cb.finished()
    assert outcome == "error" and "busy" in error
    assert chert.created == [] and rooms.log == []


async def test_line_not_ready_refuses():
    chert = FakeChert(lines=[{"id": "line-1", "status": "available", "availability": "available",
                              "session_health": {"status": "offline",
                                                 "readiness": {"facetime_signed_in": False}}}])
    cb = await run(HearthCallService(SETTINGS, rooms=FakeRooms(), chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    assert cb.finished()[0][1] == "error" and chert.created == []


async def test_idle_line_with_offline_session_and_audio_not_ready_is_ok():
    # what our healthy idle line actually reports (Sat 23:41)
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}], rooms=rooms, lines=[{
        "id": "line-1", "status": "available", "availability": "available", "operational_status": "available",
        "current_call_id": None, "session_health": {"status": "offline", "readiness": {
            "facetime_signed_in": True, "control_ready": True, "video_ready": True, "audio_ready": False}}}])
    await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
              RecordingCallbacks())
    assert len(chert.created) == 1


async def test_stuck_in_ending_before_answer_times_out_and_finishes_once():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "allocated"}, {"status": "ending"}], rooms=rooms, stuck_after_end="ending")
    t0 = time.monotonic()
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    assert cb.finished() == [("finished", "error", "the FaceTime call got stuck while ending")]
    assert len(chert.ended) == 1 and time.monotonic() - t0 < 3
    assert ("delete_room", "checkin-ck1") in rooms.log


async def test_stuck_in_ending_after_live_still_finishes_once():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}, LIVE, LIVE, {"status": "ending"}], rooms=rooms,
                      stuck_after_end="ending")
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    assert cb.kinds() == ["ringing", "connected", "finished"]
    assert cb.finished() == [("finished", None, None)]
    assert len(chert.ended) == 1


async def test_hanging_chert_end_still_releases_livekit_and_finishes():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}, LIVE], rooms=rooms)

    async def hang(call_id: str, key: str) -> int:
        await asyncio.sleep(3600)
        return 202

    chert.end_call = hang  # type: ignore[method-assign]
    t0 = time.monotonic()
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks(), max_seconds=1)
    assert len(cb.finished()) == 1 and time.monotonic() - t0 < 8
    assert ("delete_room", "checkin-ck1") in rooms.log


async def test_agent_never_joins_cleans_up():
    rooms = FakeRooms(agent_joins=False)
    chert = FakeChert()
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks())
    assert cb.finished() == [("finished", "error", "agent did not join")]
    assert chert.created == []
    assert ("delete_dispatch", "AD_1", "checkin-ck1") in rooms.log and ("delete_room", "checkin-ck1") in rooms.log


async def test_agent_with_audio_only_is_not_ready():
    rooms = FakeRooms()
    orig = rooms.dispatch_agent

    async def audio_only(room, name, md):
        d = await orig(room, name, md)
        rooms.rooms[room][-1].tracks = [t for t in rooms.rooms[room][-1].tracks if t.kind == "audio"]
        return d

    rooms.dispatch_agent = audio_only  # type: ignore[method-assign]
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=FakeChert(), timings=FAST), checkin(),
                   RecordingCallbacks())
    assert cb.finished() == [("finished", "error", "agent did not join")]


async def test_budget_expiry_ends_the_call():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}, LIVE], rooms=rooms)
    t0 = time.monotonic()
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(),
                   RecordingCallbacks(), max_seconds=1)
    assert cb.finished() == [("finished", None, "time budget reached")]
    assert 1.0 <= time.monotonic() - t0 < 4
    assert len(chert.ended) == 1


async def test_agent_leaving_while_live_ends_normally():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}, LIVE], rooms=rooms)
    cb = RecordingCallbacks(on_connected=lambda: _later(0.05, lambda: rooms.agent_leaves("checkin-ck1")))
    await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(), cb)
    assert cb.finished() == [("finished", None, None)] and len(chert.ended) == 1


async def _later(delay: float, fn) -> None:
    asyncio.get_running_loop().call_later(delay, fn)


async def test_end_twice_is_idempotent_and_finishes_once():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}], rooms=rooms)
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    c = checkin()
    cb = RecordingCallbacks()
    await svc.start(c, job(c), cb)
    await asyncio.sleep(0.1)
    await svc.end(c.id, "cancelled")
    await svc.end(c.id, "cancelled")
    await asyncio.wait_for(cb.done.wait(), 5)
    await svc.end(c.id, "cancelled")  # after finish: no-op
    await asyncio.sleep(0.1)
    assert cb.finished() == [("finished", None, None)]
    assert len(chert.ended) == 1


async def test_second_start_for_same_checkin_is_ignored():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}], rooms=rooms)
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    c = checkin()
    cb = RecordingCallbacks()
    await svc.start(c, job(c), cb)
    await svc.start(c, job(c), cb)
    await asyncio.wait_for(cb.done.wait(), 5)
    await asyncio.sleep(0.05)
    assert len(cb.finished()) == 1 and len(chert.created) == 1


async def test_callback_exceptions_do_not_break_cleanup():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}, LIVE], rooms=rooms)

    class Boom(RecordingCallbacks):
        async def call_connected(self, checkin_id: str) -> None:
            raise RuntimeError("core bug")

    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(), Boom(),
                   max_seconds=1)
    assert len(cb.finished()) == 1 and len(chert.ended) == 1


# ---------------------------------------------------------------- kiosk


async def test_kiosk_with_participant():
    rooms = FakeRooms(kiosk=True)
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=None, timings=FAST)
    c = checkin("kiosk", room="checkin-ck1")  # wrong room on purpose: the kiosk room is used

    async def end_soon():
        await _later(0.05, lambda: asyncio.ensure_future(svc.end(c.id, "done")))

    cb = await run(svc, c, RecordingCallbacks(on_connected=end_soon))
    assert cb.kinds() == ["ringing", "connected", "finished"]
    assert cb.finished() == [("finished", None, None)]
    assert ("dispatch", "kiosk-kitchen", "hearth-agent") in rooms.log
    assert ("remove", "kiosk-kitchen", "agent-AJ_1") in rooms.log
    assert not any(e[0] == "remove" and e[2] == "kiosk-kitchen" for e in rooms.log)  # never the kiosk
    assert not any(e[0] in ("delete_room", "ensure_room") for e in rooms.log)
    assert json.loads(rooms.metadata["kiosk-kitchen"][0])["hearth_live"] is True
    assert rooms.metadata["kiosk-kitchen"][-1] == "{}"


async def test_kiosk_without_participant_is_no_answer():
    rooms = FakeRooms(kiosk=False)
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=None, timings=FAST), checkin("kiosk"),
                   RecordingCallbacks())
    assert cb.finished() == [("finished", "no_answer", None)]
    assert "dispatch" not in rooms.names()


async def test_kiosk_that_never_unmutes_is_no_answer():
    rooms = FakeRooms(kiosk=True, kiosk_answers=False)
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=None, timings=FAST), checkin("kiosk"),
                   RecordingCallbacks())
    assert cb.kinds() == ["ringing", "finished"] and cb.finished()[0][1] == "no_answer"
    assert ("remove", "kiosk-kitchen", "agent-AJ_1") in rooms.log


async def test_kiosk_leaving_mid_call_ends_it():
    rooms = FakeRooms(kiosk=True)
    cb = RecordingCallbacks(on_connected=lambda: _later(0.05, lambda: rooms.rooms["kiosk-kitchen"].pop(0)))
    await run(HearthCallService(SETTINGS, rooms=rooms, chert=None, timings=FAST), checkin("kiosk"), cb)
    assert cb.finished() == [("finished", None, None)]


async def test_facetime_without_chert_falls_back_to_kiosk():
    rooms = FakeRooms(kiosk=True)
    cb = await run(HearthCallService(SETTINGS, rooms=rooms, chert=None, timings=FAST), checkin("facetime"),
                   RecordingCallbacks(), max_seconds=1)
    assert ("dispatch", "kiosk-kitchen", "hearth-agent") in rooms.log
    assert cb.kinds() == ["ringing", "connected", "finished"]


async def test_without_livekit_reports_error():
    cb = await run(HearthCallService(CallSettings(), rooms=None, chert=None, timings=FAST), checkin("kiosk"),
                   RecordingCallbacks())
    (_, outcome, error), = cb.finished()
    assert outcome == "error" and "LiveKit" in error


# ---------------------------------------------------------------- tokens, numbers, health, page


def _claims(token: str) -> dict:
    return jwt.decode(token, SETTINGS.livekit_api_secret, algorithms=["HS256"], options={"verify_aud": False})


async def test_bridge_token_claims():
    rooms = FakeRooms()
    chert = FakeChert([{"status": "dialing"}], rooms=rooms)
    await run(HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST), checkin(), RecordingCallbacks())
    cl = _claims(chert.created[0][0]["participant_token"])
    assert isinstance(cl["nbf"], int) and isinstance(cl["exp"], int)
    assert cl["exp"] - cl["nbf"] <= 7200 and cl["exp"] - time.time() > 90
    assert cl["sub"] == "chert-bridge-ck1" and cl["sub"] != chert.created[0][0]["remote_participant_identity"]
    assert cl["iss"] == SETTINGS.livekit_api_key
    # the SDK also sets canPublishData; the probe's SDK token with the same claims got HTTP 201 from Chert
    want = {"roomJoin": True, "room": "checkin-ck1", "canPublish": True, "canSubscribe": True}
    assert want.items() <= cl["video"].items()


async def test_kiosk_token_claims():
    svc = HearthCallService(SETTINGS, rooms=FakeRooms(), chert=None, timings=FAST)
    kt = await svc.kiosk_token("kitchen")
    assert kt.room == kt.identity == "kiosk-kitchen" and kt.url == SETTINGS.livekit_url
    cl = _claims(kt.token)
    assert cl["sub"] == "kiosk-kitchen" and isinstance(cl["exp"], int)
    assert cl["exp"] - cl["nbf"] == 12 * 3600
    assert cl["video"]["room"] == "kiosk-kitchen" and cl["video"]["canPublish"] and cl["video"]["canSubscribe"]


async def test_kiosk_token_rejects_bad_device_and_missing_keys():
    from hearth.calls import CallsUnavailable

    svc = HearthCallService(SETTINGS, rooms=FakeRooms(), chert=None, timings=FAST)
    with pytest.raises(CallsUnavailable):
        await svc.kiosk_token("../etc")
    with pytest.raises(CallsUnavailable):
        await HearthCallService(CallSettings(), rooms=None, chert=None).kiosk_token("kitchen")


@pytest.mark.parametrize(("raw", "want"), [
    ("4045550123", "+14045550123"), ("(404) 555-0123", "+14045550123"), ("404.555.0123", "+14045550123"),
    ("14045550123", "+14045550123"), ("+1 404 555 0123", "+14045550123"), ("+447700900123", "+447700900123"),
    ("00447700900123", "+447700900123"),
])
def test_e164(raw: str, want: str) -> None:
    assert to_e164(raw) == want


def test_mask_number() -> None:
    assert mask_number("+14045550123") == "***23" and mask_number(None) == "(none)"


def test_health() -> None:
    assert HearthCallService(SETTINGS, rooms=FakeRooms(), chert=FakeChert()).health() == {
        "livekit": "ok", "chert": "ok"}
    assert HearthCallService(CallSettings(), rooms=None, chert=None).health() == {
        "livekit": "missing_key", "chert": "missing_key"}


def test_build_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from hearth.calls import build_call_service

    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "CHERT_API_KEY", "CHERT_PROJECT_ID",
              "CHERT_LINE_ID", "CHERT_IPAD_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    assert build_call_service().health() == {"livekit": "missing_key", "chert": "missing_key"}


def test_kiosk_page_is_served() -> None:
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).get("/kiosk?key=x&device=kitchen")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "livekit-client@2.23.0" in r.text and "Tap once to get ready" in r.text
    assert r.headers["cache-control"] == "no-store"
