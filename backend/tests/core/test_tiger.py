"""Opt-in: the same happy path and /api/week on Tiger Cloud. Skipped without TIGER_DATABASE_URL.

Run: fnox exec --only TIGER_DATABASE_URL -- env WS_ENV=TIGER_DATABASE_URL \
       scripts/ws 'cd backend && uv run pytest -q tests/core/test_tiger.py'
It re-seeds the `hearth` schema (same as POST /api/demo/reset).
"""

from __future__ import annotations

import os

import pytest
from hearth_contracts import Today, Week

from .conftest import wait_ended

pytestmark = pytest.mark.skipif(not os.environ.get("TIGER_DATABASE_URL"), reason="TIGER_DATABASE_URL not set")


@pytest.fixture
def tiger(make_client):
    c = make_client(database_url=os.environ["TIGER_DATABASE_URL"])
    assert c.get("/api/health").json()["integrations"]["tiger"] == "ok"
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
    days = [__import__("datetime").date.fromisoformat(d.day) for d in w1.days]
    py = c.portal.call(svc._python_day_stats, days, "America/New_York")
    for a, b in zip(w1.days, py):
        assert (a.fluid_ml, a.drinks_confirmed, a.drinks_refused, a.meals_confirmed, a.meds_confirmed,
                a.checkins_missed) == (b.fluid_ml, b.drinks_confirmed, b.drinks_refused, b.meals_confirmed,
                                       b.meds_confirmed, b.checkins_missed), (a, b)
    hist = w1.days[-4:-1]
    assert sum(d.meds_confirmed for d in hist) == 5 and sum(d.drinks_refused for d in hist) == 1

    # Privacy delete really deletes, and the aggregates follow.
    n = c.delete("/api/privacy/data").json()["deleted_events"]
    assert n > 10
    w2 = Week.model_validate(c.get("/api/week").json())
    assert all(d.fluid_ml == 0 and d.meds_confirmed == 0 for d in w2.days)
    assert c.post("/api/demo/reset").status_code == 200
