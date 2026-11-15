"""Automatic routes and heartbeat auth with only in-memory state and fake calls."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from hearth_contracts import Checkin, StartCheckinRequest
from pydantic import ValidationError

from hearth.core.clock import DemoClock
from hearth.core.fallbacks import KeywordSearch, LogNotifier, RegexRedactor, TemplateDigest
from hearth.core.route import KioskHeartbeat, RouteService
from hearth.core.settings import Settings
from hearth.core.store import MemoryStore

from .conftest import TOKEN

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)


class FakeCalls:
    def __init__(self, chert="ok", livekit="ok"):
        self.chert, self.livekit = chert, livekit
        self.started = []

    def health(self):
        return {"livekit": self.livekit, "chert": self.chert}

    async def start(self, checkin, job, callbacks):
        self.started.append((checkin, job))
        await callbacks.call_ringing(checkin.id)


@pytest.mark.parametrize(("platform", "age", "visible", "guard", "chert", "expected", "online"), [
    ("ios", 0, True, False, "ok", "kiosk", True),
    ("android", 24.9, True, True, "ok", "kiosk", True),
    ("other", 10, True, True, "ok", "kiosk", True),
    ("ios", 25, True, True, "ok", "facetime", False),
    ("ios", 26, True, True, "ok", "facetime", False),
    ("ios", 26, True, False, "ok", "kiosk", False),
    ("ios", 0, False, True, "ok", "facetime", False),
    ("ios", 0, False, False, "ok", "kiosk", False),
    ("android", 26, True, True, "ok", "kiosk", False),
    ("android", 0, False, True, "ok", "kiosk", False),
    ("other", 26, True, True, "ok", "kiosk", False),
    (None, 0, False, True, "ok", "facetime", False),
    (None, 0, False, False, "ok", "kiosk", False),
    (None, 0, False, True, "missing_key", "kiosk", False),
    ("ios", 26, True, True, "missing_key", "kiosk", False),
    ("ios", -1, True, False, "ok", "kiosk", False),
])
def test_decide(platform, age, visible, guard, chert, expected, online):
    clock = DemoClock(MemoryStore())
    clock.now = lambda: NOW - timedelta(seconds=age)
    route = RouteService(clock, Settings(auto_facetime=guard), FakeCalls(chert))
    if platform:
        route.heartbeat(KioskHeartbeat(device="kitchen", platform=platform, visible=visible))
    clock.now = lambda: NOW
    result = route.decide()
    assert result.transport == expected
    assert result.kitchen_online is online
    assert result.platform == platform
    assert result.reason


@pytest.mark.parametrize("calls", [None, FakeCalls(livekit="missing_key")])
def test_no_available_call_service(calls):
    route = RouteService(DemoClock(MemoryStore()), Settings(auto_facetime=True), calls)
    assert route.decide().transport == "kiosk"


def test_health_failure_degrades_to_kiosk():
    class BrokenCalls:
        def health(self):
            raise RuntimeError("fake health failure")

    route = RouteService(DemoClock(MemoryStore()), Settings(auto_facetime=True), BrokenCalls())
    assert route.decide().transport == "kiosk"


def test_guard_is_exactly_one_and_default_transport_accepts_auto(monkeypatch):
    monkeypatch.delenv("HEARTH_AUTO_FACETIME", raising=False)
    assert Settings.from_env().auto_facetime is False
    for value in ("0", "true", "yes", "on", "", " 1 "):
        monkeypatch.setenv("HEARTH_AUTO_FACETIME", value)
        assert Settings.from_env().auto_facetime is False
    monkeypatch.setenv("HEARTH_AUTO_FACETIME", "1")
    monkeypatch.setenv("HEARTH_TRANSPORT", "auto")
    settings = Settings.from_env()
    assert settings.auto_facetime is True
    assert settings.transport_default == "auto"
    assert StartCheckinRequest(transport="auto").transport == "auto"
    with pytest.raises(ValidationError):
        Checkin(id="test", resident_id="test", kind="drink", transport="auto", trigger="manual",
                room="test", created_at=NOW)


@pytest.fixture
def route_client(make_client, monkeypatch):
    def make(calls=None, **kw):
        monkeypatch.setattr("hearth.core.app._build_calls", lambda: (calls, None))
        monkeypatch.setattr("hearth.core.app._build_integrations", lambda _: {
            "notifier": LogNotifier(), "search": KeywordSearch(),
            "digest": TemplateDigest(), "redactor": RegexRedactor(),
        })
        return make_client(**kw)
    return make


def test_heartbeat_auth_session_auth_and_device_filter(route_client):
    c = route_client(login=False, kiosk_device="kitchen")
    body = {"device": "kitchen", "platform": "ios", "visible": True}
    assert c.get("/api/route").status_code == 401
    for query in ("", "?key=wrong"):
        assert c.post(f"/internal/kiosk/heartbeat{query}", json=body,
                      headers={"X-Hearth-Token": TOKEN}).status_code == 401
    assert c.app.state.svc.route.last is None
    c.post("/api/auth/login", json={"passcode": "test-pass"})
    assert c.get("/api/route").json()["platform"] is None
    endpoint = f"/internal/kiosk/heartbeat?key={TOKEN}"
    assert c.post(endpoint, json={**body, "device": "bedroom"}).status_code == 204
    assert c.get("/api/route").json()["platform"] is None
    assert c.post(endpoint, json=body).status_code == 204
    result = c.get("/api/route")
    assert result.headers["cache-control"] == "no-store"
    assert result.json() == {"transport": "kiosk", "reason": "kitchen screen is up",
                             "kitchen_online": True, "platform": "ios"}
    assert c.post(endpoint, json={**body, "platform": "invalid"}).status_code == 422
    assert c.get("/api/route").json()["platform"] == "ios"


@pytest.mark.parametrize(("default", "requested", "guard", "visible", "chert", "livekit", "resolved"), [
    ("mock", "auto", False, True, "ok", "ok", "kiosk"),
    ("auto", None, True, False, "ok", "ok", "facetime"),
    ("mock", "auto", False, False, "ok", "ok", "kiosk"),
    ("mock", "auto", True, False, "missing_key", "ok", "kiosk"),
    ("mock", "auto", False, True, "missing_key", "missing_key", "mock"),
    ("mock", "facetime", False, True, "ok", "ok", "facetime"),
    ("mock", None, True, True, "ok", "ok", "mock"),
])
def test_start_stores_only_resolved_transport(route_client, caplog, default, requested, guard, visible,
                                             chert, livekit, resolved):
    calls = FakeCalls(chert, livekit)
    c = route_client(calls, transport_default=default, auto_facetime=guard)
    c.post(f"/internal/kiosk/heartbeat?key={TOKEN}",
           json={"device": "kitchen", "platform": "ios", "visible": visible})
    with caplog.at_level(logging.INFO, logger="hearth.core"):
        reply = c.post("/api/checkins", json={"kind": "drink", "transport": requested})
    assert reply.status_code == 201
    checkin = reply.json()
    assert checkin["transport"] == resolved
    assert c.get(f"/api/checkins/{checkin['id']}").json()["transport"] == resolved
    if resolved != "mock":
        saved, job = calls.started[0]
        assert saved.transport == job.transport == resolved
    if requested == "auto" or (requested is None and default == "auto"):
        assert "route: auto ->" in caplog.text
        if visible:
            assert "route: auto -> kiosk (kitchen screen is up)" in caplog.text
    else:
        assert "route: auto ->" not in caplog.text


def test_heartbeat_key_is_removed_from_access_log(route_client):
    route_client()
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, '%s - "%s %s HTTP/%s" %d',
                               ("local", "POST", "/internal/kiosk/heartbeat?key=private-kiosk-key",
                                "1.1", 204), None)
    assert logging.getLogger("uvicorn.access").filter(record)
    assert "private-kiosk-key" not in record.getMessage()
    assert "/internal/kiosk/heartbeat" in record.getMessage()
