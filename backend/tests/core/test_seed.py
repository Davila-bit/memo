"""Seed invariants and event round trips, without keys or calls."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hearth_contracts import HearthEvent

from hearth.core.clock import local_day_bounds
from hearth.core.pgstore import EVENT_COLS, PgStore
from hearth.core.seed import TZ, history, schedule
from hearth.core.store import MemoryStore


@pytest.mark.parametrize("hour,minute", [(0, 0), (8, 15), (14, 0), (20, 0)])
def test_seed_is_deterministic_and_only_so_far(hour, minute):
    now = datetime(2026, 11, 15, hour, minute, tzinfo=ZoneInfo(TZ))
    items = schedule()
    events = history(now, items)
    assert events == history(now, items)
    assert all(e.ts <= now and e.for_kind in ("drink", "meal", "med") for e in events)
    full = history(now.replace(hour=23, minute=59), items)
    assert events == [e for e in full if e.ts <= now]
    assert len({e.ts.astimezone(ZoneInfo(TZ)).date() for e in events}) == (6 if hour < 8 else 7)
    refused = [e for e in events if e.kind == "refused"]
    missed = [e for e in events if e.kind == "missed"]
    assert {e.said for e in refused} == {"not thirsty, had tea", "said she'd have some later"}
    for notable in (refused, missed):
        assert len(notable) == len({e.ts.astimezone(ZoneInfo(TZ)).date() for e in notable}) == 2
    assert all(e.for_kind == "med" for e in missed)


@pytest.mark.parametrize("kind", ["refused", "missed", "concern", "note"])
async def test_event_for_kind_round_trip(kind, monkeypatch):
    event = HearthEvent(id=f"ev-{kind}", ts=datetime(2026, 11, 15, tzinfo=UTC), resident_id="margaret",
                        kind=kind, for_kind="med", source="mock", summary="Tablets not observed")
    memory = MemoryStore()
    await memory.add_event(event)
    assert await memory.list_events() == [event]
    # The exact typed-column representation written to PgStore is also accepted on read.
    row = dict(zip(EVENT_COLS, PgStore._event_vals(event), strict=True))
    pg = PgStore("postgresql://unused")

    async def rows(sql, params):
        assert "for_kind" in sql
        return [row]

    monkeypatch.setattr(pg, "_rows", rows)
    assert await pg.list_events() == [event]


async def test_aggregate_refresh_windows_exclude_today(monkeypatch):
    today = datetime.now(UTC).astimezone(ZoneInfo(TZ)).date()
    today_start, _ = local_day_bounds(today, TZ)
    days = [today - timedelta(days=back) for back in range(6, -1, -1)]
    pg = PgStore("postgresql://unused")
    windows = []

    async def refresh(start, end):
        windows.append((start, end))

    async def rows(sql, params):
        return []

    monkeypatch.setattr(pg, "refresh_aggregates", refresh)
    monkeypatch.setattr(pg, "_rows", rows)
    await pg.refresh_history(today_start - timedelta(days=6))
    await pg.day_stats(days, TZ)
    assert len(windows) == 2
    assert all(start < end == today_start for start, end in windows)
