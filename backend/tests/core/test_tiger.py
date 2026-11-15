"""Opt-in: the same happy path and /api/week on Tiger Cloud. Skipped without TIGER_DATABASE_URL.

Run: fnox exec --only TIGER_DATABASE_URL -- env WS_ENV=TIGER_DATABASE_URL \
       scripts/ws 'cd backend && uv run pytest -q tests/core/test_tiger.py'
It re-seeds the `hearth` schema (same as POST /api/demo/reset).
"""

from __future__ import annotations

import os
from datetime import date
from zoneinfo import ZoneInfo

import pytest
from hearth_contracts import Today, Week

from hearth.core.seed import TZ, history, schedule

from .conftest import wait_ended

pytestmark = pytest.mark.skipif(not os.environ.get("TIGER_DATABASE_URL"), reason="TIGER_DATABASE_URL not set")


@pytest.fixture
def tiger(make_client):
    c = make_client(database_url=os.environ["TIGER_DATABASE_URL"])
    assert c.get("/api/health").json()["integrations"]["tiger"] == "ok"
    # Startup migration must be safe to apply again on an existing schema.
    c.portal.call(c.app.state.svc.store.apply_schema)
    assert c.post("/api/demo/reset").status_code == 200
    return c


def test_tiger_happy_path_and_week(tiger):
    c = tiger
    w0 = Week.model_validate(c.get("/api/week").json())
    assert w0.source == "tiger_continuous_aggregate" and len(w0.days) == 7

    before = Today.model_validate(c.get("/api/today").json())
    chk = c.post("/api/checkins", json={"kind": "drink"}).json()
    done = wait_ended(c, chk["id"], timeout=20)
    assert done["outcome"] == "confirmed"
    after = Today.model_validate(c.get("/api/today").json())
    assert after.fluid_ml == before.fluid_ml + 180
    assert after.events[0].checkin_id == chk["id"]

    # Today's drink shows up in the continuous aggregate straight away.
    w1 = Week.model_validate(c.get("/api/week").json())
    today = [d for d in w1.days if d.drinks_confirmed == w0.days[-1].drinks_confirmed + 1]
    assert w1.days[-1].fluid_ml == w0.days[-1].fluid_ml + 180, (w0.days[-1], w1.days[-1])
    assert today

    # The aggregates agree with a plain Python count over the same rows.
    svc = c.app.state.svc
    days = [date.fromisoformat(d.day) for d in w1.days]
    py = c.portal.call(svc._python_day_stats, days, "America/New_York")
    for a, b in zip(w1.days, py):
        assert (a.fluid_ml, a.drinks_confirmed, a.drinks_refused, a.meals_confirmed, a.meds_confirmed,
                a.checkins_missed) == (b.fluid_ml, b.drinks_confirmed, b.drinks_refused, b.meals_confirmed,
                                       b.meds_confirmed, b.checkins_missed), (a, b)
    hist = w1.days[:-1]
    seeded = history(before.now, schedule())
    prior = [e for e in seeded if e.ts.astimezone(ZoneInfo(TZ)).date().isoformat() != w1.days[-1].day]
    assert all(450 <= d.fluid_ml <= 1150 for d in hist)
    assert sum(d.meds_confirmed for d in hist) == sum(e.kind == "med" for e in prior)
    assert sum(d.drinks_refused for d in hist) == sum(e.kind == "refused" for e in prior)
    # Refusals and missed tablet calls survive the database round trip with their purpose.
    evs = c.get("/api/events", params={"limit": 500}).json()
    assert all(e["for_kind"] == "drink" for e in evs if e["kind"] == "refused")
    assert all(e["for_kind"] == "med" for e in evs if e["kind"] == "missed")

    # Privacy delete really deletes, and the aggregates follow.
    n = c.delete("/api/privacy/data").json()["deleted_events"]
    assert n > 10
    w2 = Week.model_validate(c.get("/api/week").json())
    assert all(d.fluid_ml == 0 and d.meds_confirmed == 0 for d in w2.days)
    assert c.post("/api/demo/reset").status_code == 200
