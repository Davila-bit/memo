"""Spoken reports complete a check-in without becoming visual observations."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hearth.core.clock import DemoClock

from .conftest import internal, wait_ended


@pytest.fixture
def reported_client(make_client, monkeypatch):
    # Keep today/week boundaries and expected medication counts fixed during comparisons.
    monkeypatch.setattr(DemoClock, "now", lambda self: datetime(2026, 11, 15, 15, tzinfo=UTC))
    return make_client


@pytest.mark.parametrize("kind,summary,title", [
    ("drink", "Said she had some water.", "Mum says she had her water"),
    ("meal", "Said she has eaten.", "Mum says she has eaten"),
    ("med", "Said she took her morning tablets.", "Mum says she took her morning tablets"),
])
def test_mock_report_completes_without_alert_or_observed_totals(reported_client, kind, summary, title):
    c = reported_client()
    notify = AsyncMock()
    c.app.state.svc.notifier = SimpleNamespace(notify=notify)
    before = c.get("/api/today").json()
    week = c.get("/api/week").json()
    alerts = c.get("/api/alerts", params={"status": "all"}).json()
    response = c.post("/api/checkins", json={"kind": kind, "transport": "mock", "mock_outcome": "reported"})
    assert response.status_code == 201, response.text
    cid = response.json()["id"]
    done = wait_ended(c, cid)
    assert done["outcome"] == "reported" and done["error"] is None
    assert done["summary"] == summary + " The helper couldn't see her on this call."
    after = c.get("/api/today").json()
    for key in ("fluid_ml", "drinks_confirmed", "meals_confirmed", "meds_confirmed"):
        assert after[key] == before[key]
    assert c.get("/api/week").json() == week
    assert c.get("/api/alerts", params={"status": "all"}).json() == alerts
    events = [e for e in after["events"] if e["checkin_id"] == cid]
    assert len(events) == 1  # no missed event at finish
    event = events[0]
    assert event["kind"] == "note" and event["for_kind"] == kind and event["source"] == "mock"
    assert event["confidence"] == 0.4 and event["evidence"] == f"Said: {event['said']}"
    assert event["volume_ml_est"] is None and event["med_steps"] is None and event["amount"] is None
    notify.assert_awaited_once_with(title, "The helper heard her say so but couldn't see her on this call.",
                                    urgent=False, url="/", actions=None)


@pytest.mark.parametrize("kind", ["drink", "meal", "med"])
@pytest.mark.parametrize("reason", ["done", "time_budget", "error"])
def test_internal_report_is_set_once_redacted_and_kept_at_finish(reported_client, kind, reason):
    c = reported_client(mock_scale=100)
    cid = c.post("/api/checkins", json={"kind": kind, "transport": "mock"}).json()["id"]
    internal(c, cid, "status", {"status": "connected"})
    body = {"confidence": 0.4, "evidence": "Said:   I've done it. Call 404-555-0123   "}
    body.update({"steps_seen": []} if kind == "med" else {"amount": "all"})
    response = internal(c, cid, f"confirm_{kind}", body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] and result["say"] == "Thank her and say goodbye."
    assert c.get(f"/api/checkins/{cid}").json()["outcome"] == "reported"
    event = next(e for e in c.get("/api/events").json() if e["id"] == result["event_id"])
    assert event["said"] == "I've done it. Call [phone]"
    assert "404-555-0123" not in event["evidence"] and event["evidence"].startswith("Said:")
    assert event["for_kind"] == kind and event["confidence"] == 0.4
    assert internal(c, cid, f"confirm_{kind}", body).json()["event_id"] is None
    assert internal(c, cid, "end", {"reason": reason}).status_code == 200
    done = c.get(f"/api/checkins/{cid}").json()
    assert done["status"] == "ended" and done["outcome"] == "reported" and done["error"] is None
    assert [e["kind"] for e in c.get("/api/events").json() if e["checkin_id"] == cid] == ["note"]
    assert not [a for a in c.get("/api/alerts", params={"status": "all"}).json() if a["checkin_id"] == cid]


@pytest.mark.parametrize("confidence,outcome,kind", [(0.499, "reported", "note"), (0.5, "confirmed", "drink")])
def test_said_prefix_obeys_confidence_boundary(reported_client, confidence, outcome, kind):
    c = reported_client(mock_scale=100)
    cid = c.post("/api/checkins", json={"kind": "drink"}).json()["id"]
    internal(c, cid, "confirm_drink", {"amount": "some", "confidence": confidence,
                                       "evidence": "Said: I've had water"})
    # Transport completion with an error must also preserve a tool-set outcome.
    c.portal.call(c.app.state.svc.call_finished, cid, None, "time budget reached")
    done = c.get(f"/api/checkins/{cid}").json()
    assert done["outcome"] == outcome and done["error"] is None
    events = [e for e in c.get("/api/events").json() if e["checkin_id"] == cid]
    assert len(events) == 1 and events[0]["kind"] == kind


@pytest.mark.parametrize("evidence", ["maybe she drank", "said: I've had water", "She Said: I've had water"])
def test_other_low_confirms_remain_pending(reported_client, evidence):
    c = reported_client(mock_scale=100)
    cid = c.post("/api/checkins", json={"kind": "drink"}).json()["id"]
    response = internal(c, cid, "confirm_drink", {"amount": "some", "confidence": 0.4, "evidence": evidence})
    assert "once more" in response.json()["say"]
    assert c.get(f"/api/checkins/{cid}").json()["outcome"] is None
    event = next(e for e in c.get("/api/events").json() if e["checkin_id"] == cid)
    assert event["kind"] == "note" and event["said"] is None
