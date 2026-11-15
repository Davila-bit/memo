from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace

import jwt
import pytest

from hearth.calls import CallsUnavailable, HearthCallService
from hearth.calls.livekit_ops import ParticipantState

from .fakes import FAST, SETTINGS, FakeChert, FakeRooms, RecordingCallbacks, checkin, ev, job

LIVE = {"status": "active", "facetime_call_events": [ev("bridge_ready")]}


async def test_accept_shape_claims_and_does_not_require_published_tracks():
    class SilentAgent(FakeRooms):
        async def dispatch_agent(self, room, agent_name, metadata):
            result = await super().dispatch_agent(room, agent_name, metadata)
            self.rooms[room][-1].tracks.clear()
            return result

    rooms, chert, cb = SilentAgent(), FakeChert(), RecordingCallbacks()
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    c = checkin()
    before = time.monotonic()
    payload = await svc.accept_inbound(c, job(c).model_copy(update={"inbound": True}), cb, "incoming-1")
    assert time.monotonic() - before < 2
    assert set(payload) == {"action", "livekit_url", "participant_token", "remote_participant_identity"}
    assert payload["action"] == "accept" and payload["livekit_url"] == SETTINGS.livekit_url
    assert payload["remote_participant_identity"] == "agent-AJ_1"
    claims = jwt.decode(payload["participant_token"], SETTINGS.livekit_api_secret, algorithms=["HS256"])
    assert claims["sub"] == "chert-bridge-ck1" and claims["iss"] == SETTINGS.livekit_api_key
    assert isinstance(claims["nbf"], int) and isinstance(claims["exp"], int)
    assert 90 < claims["exp"] - time.time() <= 7200
    assert claims["exp"] - claims["nbf"] <= 7200
    assert {"room": c.room, "roomJoin": True, "canPublish": True, "canSubscribe": True}.items() <= claims["video"].items()
    assert json.loads(rooms.last_metadata)["inbound"] is True
    assert not chert.created
    await svc.end(c.id, "done")
    await asyncio.wait_for(cb.done.wait(), 2)
    assert cb.kinds() == ["ringing", "finished"]
    assert len(chert.ended) == 1


async def test_slow_agent_identity_fallback_returns_before_five_seconds(monkeypatch):
    # A participant can join well before it publishes usable tracks. No tracks are awaited.
    monkeypatch.setattr("hearth.calls.service.INBOUND_IDENTITY_SECONDS", 0.08)
    rooms, chert, cb = FakeRooms(agent_joins=False), FakeChert(), RecordingCallbacks()
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    c = checkin()
    before = time.monotonic()
    payload = await svc.accept_inbound(c, job(c), cb, "incoming-slow")
    assert 0.08 <= time.monotonic() - before < 2
    assert payload["remote_participant_identity"] == SETTINGS.agent_name
    rooms.rooms[c.room].append(ParticipantState("agent-late", is_agent=True))
    chert.script = [LIVE]
    async with asyncio.timeout(1):
        while "connected" not in cb.kinds():
            await asyncio.sleep(0.01)
    await svc.end(c.id, "done")
    await asyncio.wait_for(cb.done.wait(), 2)
    assert cb.kinds() == ["ringing", "connected", "finished"]


async def test_inbound_requires_active_and_worker_bridge_ready_then_sets_metadata():
    rooms, cb = FakeRooms(), RecordingCallbacks()
    chert = FakeChert([{"status": "active", "facetime_call_events": [ev("bridge_ready", "customer")]}], rooms=rooms)
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    c = checkin()
    await svc.accept_inbound(c, job(c), cb, "incoming-1")
    await asyncio.sleep(0.06)
    assert cb.kinds() == ["ringing"] and not rooms.metadata
    chert.script = [{"status": "dialing", "facetime_call_events": [ev("bridge_ready")]}]
    await asyncio.sleep(0.06)
    assert cb.kinds() == ["ringing"]
    chert.script = [LIVE]
    async with asyncio.timeout(1):
        while "connected" not in cb.kinds():
            await asyncio.sleep(0.01)
    assert json.loads(rooms.metadata[c.room][0]) == {"hearth_live": True, "checkin_id": c.id}
    await svc.end(c.id, "done")
    await svc.end(c.id, "done")
    await asyncio.wait_for(cb.done.wait(), 2)
    assert cb.finished() == [("finished", None, None)]
    assert rooms.metadata[c.room][-1] == "{}"
    assert ("delete_room", c.room) in rooms.log and len(chert.ended) == 1
    assert not chert.created


async def test_inbound_never_live_finishes_as_error_once():
    rooms, chert, cb = FakeRooms(), FakeChert([{"status": "active"}]), RecordingCallbacks()
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    c = checkin()
    await svc.accept_inbound(c, job(c), cb, "incoming-1")
    await asyncio.wait_for(cb.done.wait(), 2)
    assert cb.finished() == [("finished", "error", "inbound call never became ready")]
    assert ("delete_room", c.room) in rooms.log and len(chert.ended) == 1


async def test_inbound_live_budget_and_cleanup():
    rooms, cb = FakeRooms(), RecordingCallbacks()
    chert = FakeChert([LIVE], rooms=rooms)
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    c = checkin()
    await svc.accept_inbound(c, job(c, max_seconds=1), cb, "incoming-1")
    await asyncio.wait_for(cb.done.wait(), 3)
    assert cb.finished() == [("finished", None, "time budget reached")]
    assert ("delete_dispatch", "AD_1", c.room) in rooms.log and len(chert.ended) == 1


async def test_inbound_preparation_is_concurrent_and_failure_cleans_up(monkeypatch):
    monkeypatch.setattr("hearth.calls.service.INBOUND_PREP_SECONDS", 0.12)

    class BlockedRooms(FakeRooms):
        async def ensure_room(self, room, **kwargs):
            await asyncio.sleep(0.07)
            await super().ensure_room(room, **kwargs)

        async def dispatch_agent(self, room, agent_name, metadata):
            await asyncio.sleep(0.07)
            return await super().dispatch_agent(room, agent_name, metadata)

    rooms, cb = BlockedRooms(), RecordingCallbacks()
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=FakeChert(), timings=FAST)
    c = checkin()
    assert (await svc.accept_inbound(c, job(c), cb, "incoming-1"))["action"] == "accept"
    await svc.end(c.id, "done")
    await asyncio.wait_for(cb.done.wait(), 2)

    class HungRooms(BlockedRooms):
        async def dispatch_agent(self, room, agent_name, metadata):
            await asyncio.sleep(60)

    rooms, cb = HungRooms(), RecordingCallbacks()
    svc = HearthCallService(SETTINGS, rooms=rooms, chert=FakeChert(), timings=FAST)
    before = time.monotonic()
    with pytest.raises(CallsUnavailable):
        await svc.accept_inbound(c, job(c), cb, "incoming-2")
    assert time.monotonic() - before < 0.3
    await asyncio.wait_for(cb.done.wait(), 2)
    assert cb.finished() == [("finished", "error", "inbound preparation failed")]
    assert ("delete_room", c.room) in rooms.log


async def test_missing_services_decline_without_falling_back():
    c = checkin()
    for rooms, chert in [(None, FakeChert()), (FakeRooms(), None)]:
        svc = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
        with pytest.raises(CallsUnavailable):
            await svc.accept_inbound(c, job(c), RecordingCallbacks(), "incoming-1")


async def test_inbound_does_not_need_an_outbound_destination():
    settings = replace(SETTINGS, chert_ipad_number=None)
    assert settings.chert_ready
    c, cb = checkin(), RecordingCallbacks()
    svc = HearthCallService(settings, rooms=FakeRooms(), chert=FakeChert(), timings=FAST)
    assert (await svc.accept_inbound(c, job(c), cb, "incoming-1"))["action"] == "accept"
    await svc.end(c.id, "done")
    await asyncio.wait_for(cb.done.wait(), 2)


async def test_production_identity_wait_still_replies_within_webhook_deadline():
    c, cb = checkin(), RecordingCallbacks()
    svc = HearthCallService(SETTINGS, rooms=FakeRooms(agent_joins=False), chert=FakeChert(), timings=FAST)
    before = time.monotonic()
    payload = await svc.accept_inbound(c, job(c), cb, "incoming-slow")
    assert 2.5 <= time.monotonic() - before < 3.5
    assert payload["remote_participant_identity"] == SETTINGS.agent_name
    await svc.end(c.id, "done")
    await asyncio.wait_for(cb.done.wait(), 2)


@pytest.mark.parametrize("url", ["ws://local", "https://test.livekit.cloud", "wss://test:8443",
                                 "wss://user:password@test", "wss://test/#fragment", "wss://test:invalid"])
async def test_inbound_rejects_invalid_bridge_urls(url):
    svc = HearthCallService(replace(SETTINGS, livekit_url=url), rooms=FakeRooms(), chert=FakeChert())
    c = checkin()
    with pytest.raises(CallsUnavailable, match="secure LiveKit URL"):
        await svc.accept_inbound(c, job(c), RecordingCallbacks(), "incoming-1")
