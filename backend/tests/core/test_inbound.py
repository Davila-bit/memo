from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from hearth_contracts import ScheduleItem

from hearth.core.fallbacks import KeywordSearch, LogNotifier, RegexRedactor, TemplateDigest
from hearth.core.settings import Settings

from .conftest import TOKEN, internal

INCOMING = {
    "event_id": "event-1", "type": "call.incoming", "created_at": "2026-11-15T18:00:00Z",
    "data": {"call_id": "inbound-1", "line_id": "line-1", "media": {"audio": True, "video": True},
             "from_handle_hash": None, "occurred_at": "2026-11-15T18:00:00Z"},
}
PAYLOAD = {"action": "accept", "livekit_url": "wss://fake.livekit.cloud",
           "participant_token": "fake-token", "remote_participant_identity": "agent-fake"}


class IncomingCalls:
    def __init__(self, *, error=False, unhealthy=False, delay=0):
        self.error, self.unhealthy, self.delay = error, unhealthy, delay
        self.accepted = []
        self.ended = []

    def health(self):
        return {"livekit": "ok", "chert": "error" if self.unhealthy else "ok"}

    async def accept_inbound(self, checkin, job, cb, call_id):
        self.accepted.append((checkin, job, call_id))
        await asyncio.sleep(self.delay)
        if self.error:
            raise RuntimeError("fake failure")
        await cb.call_ringing(checkin.id)
        return dict(PAYLOAD)

    async def end(self, checkin_id, reason):
        self.ended.append(checkin_id)


@pytest.fixture
def inbound_client(make_client, monkeypatch):
    def make(calls=None, key="test-hook", login=False):
        monkeypatch.setattr("hearth.core.app._build_calls", lambda: (calls, None))
        monkeypatch.setattr("hearth.core.app._build_integrations", lambda _: {
            "notifier": LogNotifier(), "search": KeywordSearch(),
            "digest": TemplateDigest(), "redactor": RegexRedactor(),
        })
        return make_client(login=login, chert_hook_key=key)
    return make


@pytest.mark.parametrize("key", [None, "test-hook"])
def test_bad_or_unconfigured_key_returns_404(inbound_client, key):
    c = inbound_client(IncomingCalls(), key=key)
    assert c.post("/hooks/chert/wrong", json=INCOMING).status_code == 404
    if key is None:
        assert c.post("/hooks/chert/test-hook", json=INCOMING).status_code == 404


def test_hook_setting_and_access_log_redaction(monkeypatch, inbound_client):
    monkeypatch.setenv("HEARTH_CHERT_HOOK_KEY", "test-hook")
    assert Settings.from_env().chert_hook_key == "test-hook"
    inbound_client()
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, '%s - "%s %s HTTP/%s" %d',
                               ("local", "POST", "/hooks/chert/test-hook?anything=value", "1.1", 200), None)
    assert logging.getLogger("uvicorn.access").filter(record)
    assert "test-hook" not in record.getMessage() and "<redacted>" in record.getMessage()


def test_accepts_without_session_and_builds_inbound_job(inbound_client):
    calls = IncomingCalls()
    c = inbound_client(calls)
    c.app.state.svc.clock.now = lambda: datetime(2026, 11, 15, 18, tzinfo=UTC)  # 13:00 local
    before = time.monotonic()
    reply = c.post("/hooks/chert/test-hook", json=INCOMING)
    assert reply.status_code == 200 and reply.json() == PAYLOAD
    assert time.monotonic() - before < 2
    checkin, job, call_id = calls.accepted[0]
    assert call_id == "inbound-1" and checkin.trigger == "inbound" and checkin.transport == "facetime"
    assert checkin.schedule_item_id == "water-1300" and checkin.cue_id == "cue-mug"
    assert job.inbound and job.kind == "drink" and job.caller_identity_prefix == "chert-bridge-"
    assert c.portal.call(c.app.state.svc.store.active_checkin).status == "ringing"


@pytest.mark.parametrize(("hour", "minute", "expected"), [(8, 15, "med"), (12, 30, "meal"), (3, 0, "drink")])
def test_nearest_enabled_schedule_or_default(inbound_client, hour, minute, expected):
    calls = IncomingCalls()
    c = inbound_client(calls)
    # UTC date/time chosen from local November times (UTC-5).
    c.app.state.svc.clock.now = lambda: datetime(2026, 11, 16 if hour >= 19 else 15,
                                               (hour + 5) % 24, minute, tzinfo=UTC)
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == PAYLOAD
    checkin, job, _ = calls.accepted[0]
    assert checkin.kind == job.kind == expected
    assert job.cue and job.cue.kind == expected
    if hour == 3:
        assert checkin.schedule_item_id is None


def test_schedule_uses_adjacent_days_ignores_disabled_and_keeps_custom_cue(inbound_client):
    calls = IncomingCalls()
    c = inbound_client(calls)
    svc = c.app.state.svc
    svc.clock.now = lambda: datetime(2026, 11, 15, 5, 10, tzinfo=UTC)  # 00:10 local
    c.portal.call(svc.store.put_schedule_item, ScheduleItem(
        id="night-meal", resident_id="margaret", kind="meal", time="23:50", label="Late meal", cue_id="cue-plate"))
    c.portal.call(svc.store.put_schedule_item, ScheduleItem(
        id="disabled", resident_id="margaret", kind="med", time="00:10", label="Disabled", enabled=False))
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == PAYLOAD
    checkin, job, _ = calls.accepted[0]
    assert checkin.schedule_item_id == "night-meal" and job.kind == "meal" and job.cue.id == "cue-plate"


def test_busy_declines_and_duplicate_decline_stays_declined(inbound_client):
    calls = IncomingCalls()
    c = inbound_client(calls, login=True)
    active = c.post("/api/checkins", json={"kind": "drink", "transport": "mock"}).json()
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == {"action": "decline"}
    c.post(f"/api/checkins/{active['id']}/cancel")
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == {"action": "decline"}
    assert not calls.accepted


@pytest.mark.parametrize("calls", [None, IncomingCalls(unhealthy=True)])
def test_unavailable_declines_without_creating_checkin(inbound_client, calls):
    c = inbound_client(calls)
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == {"action": "decline"}
    assert c.portal.call(c.app.state.svc.store.active_checkin) is None


def test_exception_declines_and_finishes_as_error(inbound_client):
    calls = IncomingCalls(error=True)
    c = inbound_client(calls)
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == {"action": "decline"}
    checkin = calls.accepted[0][0]
    stored = c.portal.call(c.app.state.svc.store.get_checkin, checkin.id)
    assert stored.status == "ended" and stored.outcome == "error"
    assert c.portal.call(c.app.state.svc.store.active_checkin) is None
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == {"action": "decline"}
    assert len(calls.accepted) == 1


def test_slow_accept_is_cancelled_before_chert_deadline_and_ends_checkin(inbound_client):
    calls = IncomingCalls(delay=60)
    c = inbound_client(calls)
    before = time.monotonic()
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == {"action": "decline"}
    assert time.monotonic() - before < 5
    checkin = c.portal.call(c.app.state.svc.store.get_checkin, calls.accepted[0][0].id)
    assert checkin.status == "ended" and checkin.outcome == "error"


def test_concurrent_duplicate_returns_same_accept_without_second_checkin(inbound_client):
    calls = IncomingCalls(delay=0.08)
    c = inbound_client(calls)
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda _: c.post("/hooks/chert/test-hook", json=INCOMING).json(), range(2)))
    assert replies == [PAYLOAD, PAYLOAD]
    assert c.post("/hooks/chert/test-hook", json=INCOMING).json() == PAYLOAD
    assert len(calls.accepted) == 1
    different = {**INCOMING, "data": {**INCOMING["data"], "call_id": "inbound-2"}}
    assert c.post("/hooks/chert/test-hook", json=different).json() == {"action": "decline"}
    assert len(calls.accepted) == 1


@pytest.mark.parametrize("kind", ["ping", "call.started", "call.ended"])
def test_other_events_are_fast_acknowledgements(inbound_client, kind):
    c = inbound_client(IncomingCalls())
    assert c.post("/hooks/chert/test-hook", json={"type": kind}).json() == {"ok": True}
    assert c.portal.call(c.app.state.svc.store.active_checkin) is None


@pytest.mark.parametrize("data", [{}, {"call_id": None}, {"call_id": 1}, {"call_id": " "}])
def test_invalid_incoming_fails_closed(inbound_client, data):
    c = inbound_client(IncomingCalls())
    assert c.post("/hooks/chert/test-hook", json={"type": "call.incoming", "data": data}).json() == {"action": "decline"}


def test_inbound_outcome_event_preserves_source_for_timeline(inbound_client):
    calls = IncomingCalls()
    c = inbound_client(calls, login=True)
    c.post("/hooks/chert/test-hook", json=INCOMING)
    checkin = calls.accepted[0][0]
    assert internal(c, checkin.id, "confirm_drink", {"amount": "sip", "confidence": 0.9,
                                                    "evidence": "mug to mouth then tilted"}, TOKEN).status_code == 200
    event = next(e for e in c.get("/api/events").json() if e["checkin_id"] == checkin.id)
    assert event["source"] == "inbound"
