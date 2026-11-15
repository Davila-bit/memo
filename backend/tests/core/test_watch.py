"""Opt-in watch behavior with fake classifiers; no media persistence or network."""

import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from hearth_contracts import Checkin

from hearth.core.settings import Settings
from hearth.integrations.gemini import WatchClassification

from .conftest import TOKEN

JPEG = b"\xff\xd8\xff" + b"ephemeral test image" + b"\xff\xd9"


def frame(client, data=JPEG):
    return client.post(f"/internal/watch/frame?key={TOKEN}", content=data,
                       headers={"Content-Type": "image/jpeg"})


def classifier(client, status="ok", note="Sitting at the table", visible=True):
    fake = AsyncMock(return_value=WatchClassification(person_visible=visible, status=status, note=note))
    client.app.state.watch.classify = fake
    return fake


def test_flag_defaults_off_and_only_exact_one_enables(monkeypatch):
    monkeypatch.delenv("HEARTH_WATCH", raising=False)
    assert Settings.from_env().watch is False
    for value in ("0", "true", "yes", ""):
        monkeypatch.setenv("HEARTH_WATCH", value)
        assert Settings.from_env().watch is False
    monkeypatch.setenv("HEARTH_WATCH", "1")
    assert Settings.from_env().watch is True


def test_flag_off_has_no_ui_or_privacy_change(client):
    fake = classifier(client)
    assert client.get("/api/me").json()["watch"] is False
    assert client.get("/api/watch").json() == {"enabled": False, "active": False, "until": None, "notes": []}
    assert client.post("/api/watch", json={"minutes": 30}).status_code == 404
    assert client.post("/api/watch", json={"minutes": 45}).status_code == 404
    assert client.post("/api/watch", json={}).status_code == 404
    assert client.delete("/api/watch").status_code == 404
    assert client.get(f"/internal/watch/state?key={TOKEN}").status_code == 404
    assert frame(client).status_code == 404
    assert "const WATCH_ENABLED = false" in client.get("/kiosk").text
    assert not any("While watch mode" in line for line in client.get("/api/privacy").json()["in_transit"])
    fake.assert_not_awaited()


def test_session_and_exact_kiosk_key_required(make_client):
    c = make_client(watch=True, login=False)
    assert c.get("/api/watch").status_code == 401
    assert c.post("/api/watch", json={"minutes": 30}).status_code == 401
    assert c.delete("/api/watch").status_code == 401
    assert c.get("/internal/watch/state").status_code == 401
    # The existing agent header alone does not replace the kiosk query key.
    assert c.get("/internal/watch/state", headers={"X-Hearth-Token": TOKEN}).status_code == 401
    assert c.get(f"/internal/watch/state?key={TOKEN}").json() == {"active": False}


def test_expiry_stop_and_restart_use_app_clock(make_client):
    c = make_client(watch=True)
    assert c.get("/api/me").json()["watch"] is True
    assert "const WATCH_ENABLED = true" in c.get("/kiosk").text
    assert any("While watch mode" in line for line in c.get("/api/privacy").json()["in_transit"])
    assert c.post("/api/watch", json={"minutes": 45}).status_code == 422
    assert c.post("/api/watch", json={"minutes": 30}).json()["active"] is True
    c.post("/api/demo/clock", json={"advance_minutes": 31})
    assert c.get("/api/watch").json()["active"] is False
    assert frame(c).status_code == 409
    assert c.post("/api/watch", json={"minutes": 120}).json()["active"] is True
    classifier(c)
    assert frame(c).status_code == 200
    assert c.delete("/api/watch").json()["active"] is False
    restarted = c.post("/api/watch", json={"minutes": 30}).json()
    assert restarted["active"] and restarted["notes"] == []


def test_two_concerns_alert_exactly_once_and_reset_on_restart(make_client):
    c = make_client(watch=True)
    s = c.app.state.svc
    s.notifier = AsyncMock()
    s.search = AsyncMock()
    c.post("/api/watch", json={"minutes": 30})
    classifier(c, "concern", "Lying on the floor")
    before = len(c.get("/api/alerts?status=all").json())
    assert frame(c).status_code == 200
    alerts = c.get("/api/alerts?status=all").json()
    assert len(alerts) == before + 1  # one look: a quiet amber alert only
    quiet = next(a for a in alerts if a["title"] == "The kitchen screen saw something worth a look")
    assert quiet["tier"] == "check" and quiet["kind"] == "concern" and quiet["detail"] == "Lying on the floor"
    s.notifier.notify.assert_not_awaited()
    assert frame(c).status_code == 200
    alerts = c.get("/api/alerts?status=all").json()
    assert len(alerts) == before + 2
    alert = next(a for a in alerts if a["title"] == "Mum may have fallen")
    assert alert["detail"] == "Lying on the floor" and alert["tier"] == "urgent"
    assert s.notifier.notify.await_count == 1
    assert s.notifier.notify.call_args.kwargs["urgent"] is True
    assert frame(c).status_code == 200
    assert s.notifier.notify.await_count == 1
    assert len(c.get("/api/watch").json()["notes"]) == 1
    events = c.get("/api/events?kind=concern").json()
    assert events[0]["concern_category"] == "on_floor_or_out_of_view"
    c.post("/api/watch", json={"minutes": 30})
    frame(c)
    frame(c)
    assert s.notifier.notify.await_count == 2


def test_ok_and_failures_break_consecutive_concerns(make_client):
    c = make_client(watch=True)
    c.app.state.svc.notifier = AsyncMock()
    c.post("/api/watch", json={"minutes": 30})
    for status in ("concern", "ok", "concern", None, "concern"):
        if status is None:
            c.app.state.watch.classify = AsyncMock(return_value=None)
        else:
            classifier(c, status, "On the floor" if status == "concern" else "At the table")
        assert frame(c).status_code == 200
    c.app.state.svc.notifier.notify.assert_not_awaited()
    # Three separate single looks inside 5 minutes: one quiet alert, never urgent.
    quiet = [a for a in c.get("/api/alerts?status=all").json() if a["kind"] == "concern"]
    assert [a["tier"] for a in quiet] == ["check"]


def test_quiet_alert_at_most_every_five_minutes(make_client):
    c = make_client(watch=True)
    c.app.state.svc.notifier = AsyncMock()
    c.post("/api/watch", json={"minutes": 30})
    for status, advance in (("concern", 0), ("ok", 0), ("concern", 2), ("ok", 0), ("concern", 4)):
        if advance:
            c.post("/api/demo/clock", json={"advance_minutes": advance})
        classifier(c, status, "On the floor" if status == "concern" else "At the table")
        assert frame(c).status_code == 200
    quiet = [a for a in c.get("/api/alerts?status=all").json() if a["tier"] == "check" and a["kind"] == "concern"]
    assert len(quiet) == 2
    c.app.state.svc.notifier.notify.assert_not_awaited()


def test_out_of_view_adds_one_unusual_note_no_alert(make_client):
    c = make_client(watch=True)
    c.app.state.svc.notifier = AsyncMock()
    c.post("/api/watch", json={"minutes": 30})
    classifier(c, "unusual", "Out of view", visible=False)
    frame(c)
    c.post("/api/demo/clock", json={"advance_minutes": 6})
    for _ in range(3):
        frame(c)
    notes = c.get("/api/watch").json()["notes"]
    assert sum(n["note"] == "Out of view for a while" for n in notes) == 1
    assert next(n for n in notes if n["note"] == "Out of view for a while")["status"] == "unusual"
    c.app.state.svc.notifier.notify.assert_not_awaited()


def test_frame_rejected_while_live_without_classifier(make_client):
    c = make_client(watch=True)
    fake = classifier(c)
    c.post("/api/watch", json={"minutes": 30})
    # Insert a live check-in directly: never place even a mock call in this test.
    checkin = Checkin(id="watch-busy", resident_id="res-margaret", kind="drink", transport="mock",
                      trigger="manual", room="test-room", created_at=datetime.now(UTC), status="watching")
    c.portal.call(c.app.state.svc.store.put_checkin, checkin)
    assert frame(c).status_code == 409
    assert c.get(f"/internal/watch/state?key={TOKEN}").json() == {"active": False}
    fake.assert_not_awaited()


def test_classification_failure_stores_nothing(make_client):
    c = make_client(watch=True)
    c.post("/api/watch", json={"minutes": 30})
    c.app.state.watch.classify = AsyncMock(return_value=None)
    s = c.app.state.svc
    s.search = AsyncMock()
    s.notifier = AsyncMock()
    before = c.get("/api/privacy").json()["counts"]
    assert frame(c).json()["notes"] == []
    assert c.get("/api/privacy").json()["counts"] == before
    s.search.index.assert_not_awaited()
    s.notifier.notify.assert_not_awaited()


def test_size_type_redaction_and_last_30_notes(make_client):
    c = make_client(watch=True)
    c.post("/api/watch", json={"minutes": 30})
    fake = classifier(c)
    assert frame(c, b"x" * (300 * 1024 + 1)).status_code == 413
    assert frame(c, b"not-jpeg").status_code == 400
    assert c.post(f"/internal/watch/frame?key={TOKEN}", content=JPEG).status_code == 415
    fake.assert_not_awaited()
    for i in range(32):
        classifier(c, note=f"Sitting at the table {i} phone 404-555-1234")
        assert frame(c).status_code == 200
    notes = c.get("/api/watch").json()["notes"]
    assert len(notes) == 30
    assert all("404-555-1234" not in n["note"] for n in notes)
    assert all(len(n["note"].split()) <= 10 for n in notes)


@pytest.mark.parametrize("action", ["stop", "restart", "expire", "checkin"])
def test_in_flight_frame_cannot_survive_state_change(make_client, action):
    c = make_client(watch=True)
    watch = c.app.state.watch
    c.post("/api/watch", json={"minutes": 30})

    async def classify(_jpeg):
        if action == "stop":
            watch.stop()
        elif action == "restart":
            watch.start(120)
        elif action == "expire":
            c.app.state.svc.clock.offset_minutes += 31
        else:
            await c.app.state.svc.store.put_checkin(Checkin(
                id="inflight-busy", resident_id="res-margaret", kind="drink", transport="mock",
                trigger="manual", room="test", created_at=datetime.now(UTC), status="queued"))
        await asyncio.sleep(0)
        return WatchClassification(person_visible=True, status="concern", note="On the floor")

    watch.classify = classify
    assert frame(c).status_code == 409
    assert c.get("/api/watch").json()["notes"] == []


@pytest.mark.parametrize("endpoint", ["state", "frame"])
def test_watch_access_log_hides_kiosk_key(endpoint):
    from hearth.core.app import _WebhookAccessFilter

    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, "%s %s %s %s %s",
                               ("test-client", "POST", f"/internal/watch/{endpoint}?key=private-kiosk-key",
                                "HTTP/1.1", 200), None)
    assert _WebhookAccessFilter().filter(record)
    assert "private-kiosk-key" not in record.getMessage()
    assert f"/internal/watch/{endpoint}" in record.getMessage()


def test_family_sees_only_the_latest_checked_frame_in_memory(make_client):
    c = make_client(watch=True)
    classifier(c)
    assert c.get("/api/watch/frame").status_code == 404
    c.post("/api/watch", json={"minutes": 30})
    assert c.get("/api/watch/frame").status_code == 404
    assert frame(c).status_code == 200
    second = JPEG.replace(b"ephemeral", b"newer")
    assert frame(c, second).status_code == 200
    r = c.get("/api/watch/frame")
    assert r.status_code == 200 and r.content == second
    assert r.headers["content-type"] == "image/jpeg" and "no-store" in r.headers["cache-control"]
    c.delete("/api/watch")
    assert c.get("/api/watch/frame").status_code == 404


def test_latest_frame_needs_a_family_session(make_client):
    assert make_client(watch=True, login=False).get("/api/watch/frame").status_code == 401
