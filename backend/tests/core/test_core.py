"""Core API tests on the memory store with the mock call service. No keys needed."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from hearth_contracts import Today, Week

from hearth.core.clock import DemoClock
from hearth.core.seed import history, schedule

from .conftest import PASS, TOKEN, internal, wait_ended, wait_for


def test_health_and_auth_required(make_client):
    c = make_client(login=False)
    h = c.get("/api/health")
    assert h.status_code == 200
    body = h.json()
    assert body["transport_default"] == "mock"
    assert body["integrations"]["tiger"] == "missing_key"
    assert body["integrations"]["notifier"] in ("mock", "missing_key")  # built-in, or real without keys
    for path in ["/api/today", "/api/me", "/api/week", "/api/privacy", "/api/demo/clock", "/api/ws-ticket"]:
        assert c.get(path).status_code == 401, path
    assert c.post("/api/checkins", json={}).status_code == 401
    assert c.post("/api/auth/login", json={"passcode": "nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"passcode": PASS})
    assert r.status_code == 200
    assert r.json()["name"] == "Sarah"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
    assert c.get("/api/me").status_code == 200
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/me").status_code == 401


def test_login_rate_limit(make_client):
    c = make_client(login=False)
    codes = [c.post("/api/auth/login", json={"passcode": "wrong"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


def test_secure_cookie_behind_https_proxy(make_client):
    c = make_client(login=False)
    r = c.post("/api/auth/login", json={"passcode": PASS}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_internal_token_required(client):
    cid = client.post("/api/checkins", json={"kind": "drink"}).json()["id"]
    body = {"amount": "most", "confidence": 0.9, "evidence": "drank"}
    assert client.post(f"/internal/checkins/{cid}/confirm_drink", json=body).status_code == 401
    assert internal(client, cid, "confirm_drink", body, token="wrong").status_code == 401
    assert client.get(f"/internal/checkins/{cid}").status_code == 401
    assert client.get(f"/internal/checkins/{cid}", headers={"X-Hearth-Token": TOKEN}).status_code == 200
    assert client.get("/api/kiosk/token", params={"key": "wrong"}).status_code == 401
    assert client.get("/api/kiosk/token", params={"key": TOKEN}).status_code == 503  # no call module yet
    wait_ended(client, cid)


def test_happy_path(client):
    before = Today.model_validate(client.get("/api/today").json())
    r = client.post("/api/checkins", json={"kind": "drink"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] in ("queued", "ringing") and c["transport"] == "mock" and c["room"] == f"checkin-{c['id']}"
    assert c["cue_id"] == "cue-mug"
    # one at a time
    assert client.post("/api/checkins", json={"kind": "drink"}).status_code == 409
    done = wait_ended(client, c["id"])
    assert done["outcome"] == "confirmed"
    assert done["summary"] == "Drank most of a glass of water"
    assert done["ringing_at"] and done["connected_at"] and done["ended_at"]
    after = Today.model_validate(client.get("/api/today").json())
    assert after.fluid_ml == before.fluid_ml + 180
    assert after.drinks_confirmed == before.drinks_confirmed + 1
    assert after.active_checkin is None
    ev = after.events[0]
    assert ev.kind == "drink" and ev.checkin_id == c["id"] and ev.source == "mock" and ev.volume_ml_est == 180


def test_meal_and_med_summaries(client):
    c = client.post("/api/checkins", json={"kind": "meal"}).json()
    assert wait_ended(client, c["id"])["summary"].startswith("Had ")
    c = client.post("/api/checkins", json={"kind": "med"}).json()
    done = wait_ended(client, c["id"])
    assert done["summary"].endswith("tablets: observed taking")
    ev = client.get("/api/events", params={"kind": "med", "limit": 1}).json()[0]
    assert ev["med_steps"][-1] == "slot_shown_empty"


def test_refusal_alert_retry_later(make_client):
    c = make_client(scheduler=True)
    chk = c.post("/api/checkins", json={"kind": "drink", "mock_outcome": "refused"}).json()
    done = wait_ended(c, chk["id"])
    assert done["outcome"] == "refused"
    alerts = c.get("/api/alerts", params={"status": "open"}).json()
    assert len(alerts) == 1
    a = alerts[0]
    assert a["kind"] == "refused" and a["tier"] == "check"
    assert a["title"].startswith("Mum didn't want her ") and a["title"].endswith(" water")
    assert "not thirsty, had tea" in a["detail"]
    ev = c.get("/api/events", params={"kind": "refused", "limit": 1}).json()[0]
    assert ev["said"] == "not thirsty, had tea" and ev["for_kind"] == "drink"
    r = c.post(f"/api/alerts/{a['id']}/action", json={"action": "retry_later", "retry_minutes": 30})
    assert r.status_code == 200
    handled = r.json()
    assert handled["status"] == "handled" and handled["action"] == "retry_later" and handled["retry_at"]
    assert c.get("/api/alerts", params={"status": "open"}).json() == []
    svc = c.app.state.svc

    def retry_started():
        return [x for x in c.portal.call(svc.store.list_checkins) if x.trigger == "retry"]

    # not before the demo clock reaches retry_at
    time.sleep(0.2)
    assert not retry_started()
    assert c.post("/api/demo/clock", json={"advance_minutes": 31}).status_code == 200
    retry = wait_for(retry_started)[0]
    assert retry.kind == "drink"


def test_no_answer_chain(client):
    chk = client.post("/api/checkins", json={"kind": "drink", "mock_outcome": "no_answer"}).json()
    first = wait_ended(client, chk["id"])
    assert first["outcome"] == "no_answer" and first["attempt"] == 1
    # (No "nothing missed yet" check here: the retry runs on a short timer and can finish first on a slow runner.)
    svc = client.app.state.svc

    def second():
        cs = [x for x in client.portal.call(svc.store.list_checkins) if x.attempt == 2 and x.status == "ended"]
        return cs[0] if cs else None

    retry = wait_for(second)
    assert retry.trigger == "retry" and retry.outcome == "no_answer"
    alerts = client.get("/api/alerts", params={"status": "open"}).json()
    assert len(alerts) == 1
    a = alerts[0]
    assert a["kind"] == "no_answer" and a["tier"] == "check"
    assert a["title"].startswith("Mum didn't answer her ") and a["title"].endswith(" check-in")
    missed = [e for e in client.get("/api/events", params={"kind": "missed"}).json() if e["source"] == "mock"]
    assert len(missed) == 1 and missed[0]["checkin_id"] == retry.id and missed[0]["for_kind"] == "drink"


def test_concern_urgent_alert(client):
    chk = client.post("/api/checkins", json={"kind": "drink", "mock_outcome": "concern"}).json()
    done = wait_ended(client, chk["id"])
    assert done["outcome"] == "concern"
    a = client.get("/api/alerts", params={"status": "open"}).json()[0]
    assert a["kind"] == "concern" and a["tier"] == "urgent"
    assert a["detail"].startswith("The check-in noticed something unusual: she said 'I fell down earlier' at ")
    assert a["detail"].endswith("Please check on her now.")
    assert "detect" not in (a["title"] + a["detail"]).lower()
    ev = client.get("/api/events", params={"kind": "concern", "limit": 1}).json()[0]
    assert ev["for_kind"] == "drink"
    r = client.post(f"/api/alerts/{a['id']}/action", json={"action": "handling"})
    assert r.json()["status"] == "handled"


def test_error_outcome_logs_missed(client):
    chk = client.post("/api/checkins", json={"kind": "drink", "mock_outcome": "error"}).json()
    done = wait_ended(client, chk["id"])
    assert done["outcome"] == "error" and done["error"]
    ev = client.get("/api/events", params={"limit": 1}).json()[0]
    assert ev["kind"] == "missed" and ev["checkin_id"] == chk["id"]


def test_set_once_and_low_confidence(make_client):
    c = make_client(mock_scale=100.0)  # mock script effectively paused; we drive the tools
    chk = c.post("/api/checkins", json={"kind": "drink"}).json()
    cid = chk["id"]
    low = internal(c, cid, "confirm_drink", {"amount": "some", "confidence": 0.3, "evidence": "maybe a sip"}).json()
    assert low["ok"] and low["event_id"] and "once more" in low["say"]
    ev = c.get("/api/events", params={"limit": 1}).json()[0]
    assert ev["kind"] == "note" and ev["confidence"] == 0.3 and ev["for_kind"] == "drink"
    assert c.get(f"/api/checkins/{cid}").json()["outcome"] is None
    ok = internal(c, cid, "confirm_drink", {"amount": "all", "confidence": 0.92,
                                             "evidence": "Drank it all. Call me on 404-555-0123"}).json()
    assert ok["ok"] and ok["event_id"]
    ev = c.get("/api/events", params={"limit": 1}).json()[0]
    assert ev["kind"] == "drink" and "404-555-0123" not in ev["evidence"] and "[phone]" in ev["evidence"]
    n_before = len(c.get("/api/events", params={"limit": 500}).json())
    again = internal(c, cid, "confirm_drink", {"amount": "sip", "confidence": 0.99, "evidence": "again"}).json()
    assert again == {"ok": True, "event_id": None, "say": None}
    refusal = internal(c, cid, "report_refusal", {"said": "no", "asked_times": 1}).json()
    assert refusal["event_id"] is None
    assert len(c.get("/api/events", params={"limit": 500}).json()) == n_before
    assert c.get(f"/api/checkins/{cid}").json()["outcome"] == "confirmed"
    st = internal(c, cid, "status", {"status": "watching"})
    assert st.status_code == 200
    assert internal(c, cid, "caption", {"speaker": "helper", "text": "hi"}).status_code == 204
    end = internal(c, cid, "end", {"reason": "done"}).json()
    assert end["ok"]
    done = c.get(f"/api/checkins/{cid}").json()
    assert done["status"] == "ended" and done["outcome"] == "confirmed"
    assert done["summary"] == "Drank a full glass of water"
    assert internal(c, "chk_missing", "confirm_drink", {"amount": "sip", "confidence": 1, "evidence": ""}
                    ).status_code == 404


def test_cancel(make_client):
    c = make_client(mock_scale=100.0)
    cid = c.post("/api/checkins", json={"kind": "drink"}).json()["id"]
    r = c.post(f"/api/checkins/{cid}/cancel").json()
    assert r["status"] == "ended" and r["outcome"] is None
    assert c.post("/api/checkins", json={"kind": "drink"}).status_code == 201


def test_demo_clock_and_schedule_tick(make_client):
    c = make_client(scheduler=True)
    assert c.post("/api/demo/clock", json={"set_time": "06:00"}).status_code == 200
    c.post("/api/demo/reset")
    svc = c.app.state.svc
    clk = c.post("/api/demo/clock", json={"set_time": "15:29"}).json()
    assert clk["offset_minutes"] != 0
    time.sleep(0.3)
    assert [x for x in c.portal.call(svc.store.list_checkins) if x.trigger == "schedule"] == []
    c.post("/api/demo/clock", json={"advance_minutes": 2})

    def fired():
        return [x for x in c.portal.call(svc.store.list_checkins) if x.trigger == "schedule"]

    chk = wait_for(fired)[0]
    assert chk.schedule_item_id == "water-1530" and chk.kind == "drink"
    wait_ended(c, chk.id)
    assert len(fired()) == 1  # not again
    assert c.post("/api/demo/clock", json={"reset": True}).json()["offset_minutes"] == 0
    assert c.post("/api/demo/clock", json={"reset": True, "advance_minutes": 3}).status_code == 422


def test_week_shape(make_client, monkeypatch):
    now = datetime(2026, 11, 15, 19, tzinfo=UTC)  # 2 pm local
    monkeypatch.setattr(DemoClock, "now", lambda self: now)
    client = make_client()
    w = Week.model_validate(client.get("/api/week").json())
    assert w.source == "memory" and len(w.days) == 7 and w.fluid_target_ml == 1500
    assert [d.day for d in w.days] == sorted(d.day for d in w.days)
    assert w.days[-1].day == now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    seeded = history(now, schedule())
    for day in w.days:
        events = [e for e in seeded if e.ts.astimezone(ZoneInfo("America/New_York")).date().isoformat() == day.day]
        assert day.fluid_ml == sum(e.volume_ml_est or 0 for e in events if e.kind == "drink")
        assert day.drinks_confirmed == sum(e.kind == "drink" for e in events)
        assert day.drinks_refused == sum(e.kind == "refused" and e.for_kind == "drink" for e in events)
        assert day.checkins_missed == sum(e.kind == "missed" for e in events)
        assert day.meds_confirmed == sum(e.kind == "med" for e in events)
        assert day.meals_confirmed == sum(e.kind == "meal" for e in events)
    assert all(450 <= d.fluid_ml <= 1150 for d in w.days)
    assert [d.fluid_ml for d in w.days[:-1]] == sorted(d.fluid_ml for d in w.days[:-1])
    assert sum(d.meds_confirmed for d in w.days) == 5  # one tablet time a day, two mornings missed
    assert sum(d.meds_expected for d in w.days) == 7
    assert sum(d.drinks_refused for d in w.days) == sum(d.checkins_missed for d in w.days) == 2
    assert w.med_streak_days == 2  # today and yesterday; the previous morning dose was missed


def test_alert_status_filters(client):
    # Both statuses coexist, so each filter must exclude the other one.
    ids = []
    for _ in range(2):
        chk = client.post("/api/checkins", json={"kind": "drink", "mock_outcome": "refused"}).json()
        wait_ended(client, chk["id"])
        ids.append(client.get("/api/alerts").json()[0]["id"])
    handled_id, open_id = ids
    assert client.post(f"/api/alerts/{handled_id}/action", json={"action": "i_will_call"}).status_code == 200
    for params in ({}, {"status": "open"}):
        assert [a["id"] for a in client.get("/api/alerts", params=params).json()] == [open_id]
    assert [a["id"] for a in client.get("/api/alerts", params={"status": "handled"}).json()] == [handled_id]
    assert {a["id"] for a in client.get("/api/alerts", params={"status": "all"}).json()} == set(ids)
    assert client.get("/api/alerts", params={"status": "invalid"}).status_code == 422


def test_med_refusal_keeps_its_kind_in_week_and_search(make_client, monkeypatch):
    import hearth.integrations
    from hearth.core.fallbacks import KeywordSearch

    indexed = []

    class FakeSearch(KeywordSearch):
        async def index(self, event):
            indexed.append(event)

    monkeypatch.setattr(hearth.integrations, "build_search", FakeSearch)
    c = make_client()
    before = c.get("/api/week").json()["days"][-1]["drinks_refused"]
    chk = c.post("/api/checkins", json={"kind": "med", "mock_outcome": "refused"}).json()
    wait_ended(c, chk["id"])
    event = c.get("/api/events", params={"limit": 1}).json()[0]
    assert event["kind"] == "refused" and event["for_kind"] == "med"
    assert c.get("/api/week").json()["days"][-1]["drinks_refused"] == before
    assert next(e for e in indexed if e.id == event["id"]).for_kind == "med"


def test_privacy_delete_clears_search(make_client, monkeypatch):
    import hearth.integrations
    from hearth.core.fallbacks import KeywordSearch

    class FakeSearch(KeywordSearch):
        def __init__(self):
            self.docs = {}
            self.cleared = False

        async def index(self, event):
            self.docs[event.id] = event

        async def clear(self):
            self.docs.clear()
            self.cleared = True

    search = FakeSearch()
    monkeypatch.setattr(hearth.integrations, "build_search", lambda: search)
    c = make_client(mock_scale=100.0)
    wait_for(lambda: search.docs)
    # Deletion is refused during a call, including deletion of indexed copies.
    chk = c.post("/api/checkins", json={"kind": "med"}).json()
    assert c.delete("/api/privacy/data").status_code == 409
    assert not search.cleared
    c.post(f"/api/checkins/{chk['id']}/cancel")
    assert c.delete("/api/privacy/data").json()["deleted_events"] > 0
    assert search.cleared and search.docs == {}
    assert c.post("/api/ask", json={"q": "water"}).json()["hits"] == []


def test_today_privacy_and_delete(client):
    t = client.get("/api/today").json()
    assert t["resident"]["preferred_name"] == "Margaret" and t["fluid_target_ml"] == 1500
    assert t["resident"]["phone"] is None
    assert t["meds_expected"] == 1 and t["next_checkin"] is not None
    p = client.get("/api/privacy").json()
    assert p["counts"]["frames_stored"] == 0 and p["counts"]["recordings"] == 0 and p["counts"]["events"] > 10
    assert p["retention_days_events"] == 90 and "Google Gemini" in p["in_transit"]
    n = client.delete("/api/privacy/data").json()["deleted_events"]
    assert n == p["counts"]["events"]
    assert client.get("/api/privacy").json()["counts"]["events"] == 0
    assert client.post("/api/demo/reset").status_code == 200
    assert client.get("/api/privacy").json()["counts"]["events"] > 10


def test_reset_keeps_switched_off_times_but_full_history(client):
    full = client.get("/api/privacy").json()["counts"]["events"]
    for item in client.get("/api/schedule").json():
        client.put(f"/api/schedule/{item['id']}", json={**item, "enabled": False})
    assert client.post("/api/demo/reset").status_code == 200
    assert not any(i["enabled"] for i in client.get("/api/schedule").json())
    assert client.get("/api/privacy").json()["counts"]["events"] == full


def test_cues_slot_label_and_photo(client):
    cues = {c["id"]: c for c in client.get("/api/cues").json()}
    assert set(cues) == {"cue-mug", "cue-plate", "cue-pills"}
    label = cues["cue-pills"]["slot_label"]
    assert label in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
    evening = next(i for i in client.get("/api/schedule").json() if i["id"] == "med-1900")
    assert evening["enabled"] is False  # one tablet time a day in the demo; the evening one is there, switched off
    assert client.put("/api/schedule/med-1900", json={**evening, "enabled": True}).status_code == 200
    two = {c["id"]: c for c in client.get("/api/cues").json()}["cue-pills"]["slot_label"]
    assert two.startswith(label + " · ") and two.split(" · ")[1] in ("MORNING", "EVENING")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = client.post("/api/cues/cue-mug/photo", files={"file": ("mug.png", png, "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["image_url"]
    assert url.startswith("/media/cues/cue-mug-")
    assert client.get(url).content == png
    bad = client.post("/api/cues/cue-mug/photo", files={"file": ("x.png", b"notapng", "image/png")})
    assert bad.status_code == 415


def test_schedule_crud_and_manual_event(client):
    item = {"id": "", "resident_id": "x", "kind": "drink", "time": "16:45", "label": "Late water",
            "cue_id": "cue-mug", "enabled": True}
    r = client.post("/api/schedule", json=item)
    assert r.status_code == 201
    new = r.json()
    assert new["id"] and new["resident_id"] == "margaret"
    new["enabled"] = False
    assert client.put(f"/api/schedule/{new['id']}", json=new).json()["enabled"] is False
    assert client.delete(f"/api/schedule/{new['id']}").status_code == 204
    assert client.post("/api/schedule", json={**item, "time": "25:00"}).status_code == 422
    ev = client.post("/api/events", json={"kind": "drink", "summary": "Tea when I visited", "amount": "some"})
    assert ev.status_code == 201 and ev.json()["volume_ml_est"] == 80 and ev.json()["source"] == "manual"


def test_ask_and_digest(client):
    a = client.post("/api/ask", json={"q": "did she take her tablets?"}).json()
    assert a["engine"] == "fallback" and a["hits"]
    d = client.get("/api/digest").json()
    assert d["generated_by"] == "template" and d["text"]


def test_websocket_messages(client):
    with client.websocket_connect("/ws") as ws:  # cookie session
        first = ws.receive_json()
        assert first["type"] == "clock"
    ticket = client.get("/api/ws-ticket").json()["ticket"]
    with client.websocket_connect(f"/ws?ticket={ticket}") as ws:
        assert ws.receive_json()["type"] == "clock"
        cid = client.post("/api/checkins", json={"kind": "drink"}).json()["id"]
        seen: list[str] = []
        while "event" not in seen:
            m = ws.receive_json()
            seen.append(m["type"])
            if m["type"] == "checkin":
                assert m["checkin"]["id"] == cid
        assert "checkin" in seen and "caption" in seen
    wait_ended(client, cid)


def test_websocket_rejects_without_auth(make_client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    c = make_client(login=False)
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws?ticket=bogus") as ws:
        ws.receive_json()


def test_plugins_are_picked_up(make_client, monkeypatch):
    from fastapi import APIRouter
    from hearth_contracts import KioskToken

    import hearth.calls
    import hearth.integrations

    sent: list[tuple] = []
    started: list = []

    class FakeNotifier:
        async def notify(self, title, body, *, urgent=False, url=None, actions=None):
            sent.append((title, body, urgent, actions))

        async def alert(self, alert):
            pass

        def health(self):
            return "ok"

    class FakeCalls:
        def health(self):
            return {"livekit": "ok", "chert": "missing_key"}

        async def start(self, checkin, job, cb):
            started.append((checkin, job))
            await cb.call_ringing(checkin.id)

        async def end(self, checkin_id, reason):
            pass

        async def kiosk_token(self, device):
            return KioskToken(url="wss://x", token="t", room=f"kiosk-{device}", identity=f"kiosk-{device}")

    calls_router = APIRouter()

    @calls_router.get("/api/calls-probe")
    async def probe():
        return {"ok": True}

    subs_seen = []

    def build_notifier(get_subscriptions):
        subs_seen.append(get_subscriptions)
        return FakeNotifier()

    monkeypatch.setattr(hearth.calls, "build_call_service", lambda: FakeCalls(), raising=False)
    for name in ("build_search", "build_digest_writer", "build_redactor", "health"):
        monkeypatch.delattr(hearth.integrations, name, raising=False)
    monkeypatch.setattr(hearth.calls, "router", calls_router, raising=False)
    monkeypatch.setattr(hearth.integrations, "build_notifier", build_notifier, raising=False)
    c = make_client(transport_default="facetime")
    h = c.get("/api/health").json()["integrations"]
    assert h["livekit"] == "ok" and h["chert"] == "missing_key" and h["notifier"] == "ok" and h["search"] == "mock"
    assert c.get("/api/calls-probe").json() == {"ok": True}
    assert c.get("/api/kiosk/token", params={"key": TOKEN}).json()["room"] == "kiosk-kitchen"
    chk = c.post("/api/checkins", json={"kind": "drink"}).json()
    # FaceTime isn't healthy, so the core falls back to the kiosk on the real call service.
    assert chk["transport"] == "kiosk" and chk["room"] == "kiosk-kitchen" and chk["status"] == "ringing"
    _, job = started[0]
    assert job.caller_identity_prefix == "kiosk-" and job.cue.id == "cue-mug" and job.context
    r = internal(c, chk["id"], "confirm_drink", {"amount": "most", "confidence": 0.9, "evidence": "drank"})
    assert r.status_code == 200
    title, body, urgent, _ = sent[-1]
    assert title == "✓ Mum had some water" and body.endswith(" · drank most of a glass") and not urgent
    internal(c, chk["id"], "end", {"reason": "done"})
    assert c.get(f"/api/checkins/{chk['id']}").json()["status"] == "ended"
    # A mock request still uses the built-in mock even with a real call service loaded.
    m = c.post("/api/checkins", json={"kind": "drink", "transport": "mock"}).json()
    assert m["transport"] == "mock"
    wait_ended(c, m["id"])
    assert subs_seen and callable(subs_seen[0])

    # After live, call_finished always carries outcome=None (+ an error when the end wasn't clean).
    svc = c.app.state.svc
    late = c.post("/api/checkins", json={"kind": "drink"}).json()
    c.portal.call(svc.call_connected, late["id"])
    c.portal.call(svc.call_finished, late["id"], None, "time budget reached")
    done = c.get(f"/api/checkins/{late['id']}").json()
    assert done["outcome"] == "error" and done["error"] == "time budget reached"
    ev = c.get("/api/events", params={"limit": 1}).json()[0]
    assert ev["kind"] == "missed" and ev["checkin_id"] == late["id"]
    ok = c.post("/api/checkins", json={"kind": "drink"}).json()
    internal(c, ok["id"], "confirm_drink", {"amount": "all", "confidence": 0.9, "evidence": "drank"})
    c.portal.call(svc.call_finished, ok["id"], None, None)
    assert c.get(f"/api/checkins/{ok['id']}").json()["outcome"] == "confirmed"

    # kiosk_token raising CallsUnavailable -> 503 with its sentence.
    class CallsUnavailable(Exception):
        pass

    async def no_kiosk(device):
        raise CallsUnavailable("The kitchen tablet isn't set up yet.")

    c.app.state.real_calls.kiosk_token = no_kiosk
    r = c.get("/api/kiosk/token", params={"key": TOKEN})
    assert r.status_code == 503 and r.json()["detail"] == "The kitchen tablet isn't set up yet."
