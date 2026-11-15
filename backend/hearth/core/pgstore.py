"""PgStore: the Store interface on Tiger Cloud (PostgreSQL 18 + TimescaleDB 2.32), psycopg 3 async.

Everything lives in the `hearth` schema. `events` is a hypertable with typed columns; the other
tables keep the contract model as jsonb next to the columns we filter on. `/api/week` reads the
continuous aggregates `fluids_daily` and `adherence_daily`.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hearth_contracts import (
    Alert,
    Checkin,
    Cue,
    DayStats,
    HearthEvent,
    PushSubscription,
    Resident,
    ScheduleItem,
)
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .clock import local_day_bounds

log = logging.getLogger("hearth.core")

SQL_DIR = Path(__file__).parent / "sql"
CAGG_TZ = "America/New_York"  # the bucket timezone baked into sql/001_schema.sql

EVENT_COLS = ["id", "ts", "resident_id", "checkin_id", "for_kind", "kind", "source", "confidence",
              "summary", "evidence", "amount", "volume_ml_est", "med_steps", "said", "concern_category"]


def split_sql(text: str) -> list[str]:
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*\n\s*\n", body + "\n\n") if s.strip().rstrip(";").strip()]


class PgStore:
    name = "tiger"

    def __init__(self, url: str) -> None:
        self.url = url
        self.pool = AsyncConnectionPool(url, min_size=1, max_size=4, open=False,
                                        kwargs={"autocommit": True, "prepare_threshold": None})
        self.dirty = True  # caggs need a refresh before the next read

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=20)
        await self.apply_schema()

    async def close(self) -> None:
        await self.pool.close()

    async def apply_schema(self) -> None:
        async with self.pool.connection() as conn:
            for path in sorted(SQL_DIR.glob("*.sql")):
                for stmt in split_sql(path.read_text()):
                    try:
                        await conn.execute(stmt)
                    except (errors.DuplicateTable, errors.DuplicateObject, errors.DuplicateSchema) as e:
                        log.info("schema: already there (%s)", str(e).splitlines()[0])
        log.info("schema applied to Tiger (schema hearth)")

    async def _exec(self, sql: str, params: Any = None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(sql, params)

    async def _rows(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(sql, params)
            return await cur.fetchall()

    async def is_empty(self) -> bool:
        return not await self._rows("SELECT 1 FROM hearth.residents LIMIT 1")

    async def wipe(self) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            for t in ["events", "residents", "schedule_items", "cues", "checkins", "alerts"]:
                await conn.execute(f"DELETE FROM hearth.{t}")
            await conn.execute("DELETE FROM hearth.kv WHERE key <> 'clock_offset'")
        self.dirty = True

    # ------------------------------------------------------------------ docs
    async def _put_doc(self, table: str, key: str, model: Any, extra: dict[str, Any] | None = None) -> None:
        extra = extra or {}
        cols = ["id", *extra.keys(), "doc"]
        vals = [key, *extra.values(), Jsonb(model.model_dump(mode="json"))]
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
        ph = ", ".join(["%s"] * len(cols))
        await self._exec(f"INSERT INTO hearth.{table} ({', '.join(cols)}) VALUES ({ph}) "
                         f"ON CONFLICT (id) DO UPDATE SET {sets}", vals)

    async def get_resident(self) -> Resident | None:
        rows = await self._rows("SELECT doc FROM hearth.residents ORDER BY id LIMIT 1")
        return Resident.model_validate(rows[0]["doc"]) if rows else None

    async def put_resident(self, r: Resident) -> None:
        await self._put_doc("residents", r.id, r)

    async def list_schedule(self) -> list[ScheduleItem]:
        rows = await self._rows("SELECT doc FROM hearth.schedule_items")
        return sorted((ScheduleItem.model_validate(r["doc"]) for r in rows), key=lambda i: (i.time, i.id))

    async def get_schedule_item(self, item_id: str) -> ScheduleItem | None:
        rows = await self._rows("SELECT doc FROM hearth.schedule_items WHERE id = %s", [item_id])
        return ScheduleItem.model_validate(rows[0]["doc"]) if rows else None

    async def put_schedule_item(self, item: ScheduleItem) -> None:
        await self._put_doc("schedule_items", item.id, item, {"resident_id": item.resident_id})

    async def delete_schedule_item(self, item_id: str) -> bool:
        rows = await self._rows("DELETE FROM hearth.schedule_items WHERE id = %s RETURNING id", [item_id])
        return bool(rows)

    async def list_cues(self) -> list[Cue]:
        rows = await self._rows("SELECT doc FROM hearth.cues ORDER BY id")
        return [Cue.model_validate(r["doc"]) for r in rows]

    async def get_cue(self, cue_id: str) -> Cue | None:
        rows = await self._rows("SELECT doc FROM hearth.cues WHERE id = %s", [cue_id])
        return Cue.model_validate(rows[0]["doc"]) if rows else None

    async def put_cue(self, cue: Cue) -> None:
        await self._put_doc("cues", cue.id, cue)

    # ------------------------------------------------------------------ events
    @staticmethod
    def _event_vals(ev: HearthEvent) -> list[Any]:
        d = ev.model_dump()
        return [d[c] for c in EVENT_COLS]

    async def add_event(self, ev: HearthEvent) -> None:
        await self.add_events([ev])

    async def add_events(self, evs: list[HearthEvent]) -> None:
        if not evs:
            return
        sql = (f"INSERT INTO hearth.events ({', '.join(EVENT_COLS)}) VALUES "
               f"({', '.join(['%s'] * len(EVENT_COLS))}) ON CONFLICT DO NOTHING")
        async with self.pool.connection() as conn, conn.transaction():
            cur = conn.cursor()
            await cur.executemany(sql, [self._event_vals(e) for e in evs])
        self.dirty = True
        oldest = min(e.ts for e in evs)
        if oldest < datetime.now(UTC) - timedelta(hours=12):
            await self.refresh_history(oldest)

    async def list_events(self, *, since: datetime | None = None, until: datetime | None = None,
                          kind: str | None = None, limit: int = 100) -> list[HearthEvent]:
        where, params = [], []
        if since is not None:
            where.append("ts >= %s")
            params.append(since)
        if until is not None:
            where.append("ts < %s")
            params.append(until)
        if kind is not None:
            where.append("kind = %s")
            params.append(kind)
        sql = (f"SELECT {', '.join(EVENT_COLS)} FROM hearth.events"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY ts DESC, id DESC LIMIT %s")
        params.append(limit)
        return [HearthEvent.model_validate(r) for r in await self._rows(sql, params)]

    async def count_events(self) -> int:
        rows = await self._rows("SELECT count(*) AS n FROM hearth.events")
        return int(rows[0]["n"])

    async def delete_all_events(self) -> int:
        rows = await self._rows("WITH d AS (DELETE FROM hearth.events RETURNING 1) SELECT count(*) AS n FROM d")
        self.dirty = True
        await self.refresh_history(datetime.now(UTC) - timedelta(days=95))
        return int(rows[0]["n"])

    # ------------------------------------------------------------------ check-ins / alerts
    async def put_checkin(self, c: Checkin) -> None:
        await self._put_doc("checkins", c.id, c, {"resident_id": c.resident_id, "created_at": c.created_at,
                                                  "status": c.status})

    async def get_checkin(self, checkin_id: str) -> Checkin | None:
        rows = await self._rows("SELECT doc FROM hearth.checkins WHERE id = %s", [checkin_id])
        return Checkin.model_validate(rows[0]["doc"]) if rows else None

    async def active_checkin(self) -> Checkin | None:
        rows = await self._rows("SELECT doc FROM hearth.checkins WHERE status <> 'ended' "
                                "ORDER BY created_at DESC LIMIT 1")
        return Checkin.model_validate(rows[0]["doc"]) if rows else None

    async def list_checkins(self, limit: int = 50) -> list[Checkin]:
        rows = await self._rows("SELECT doc FROM hearth.checkins ORDER BY created_at DESC LIMIT %s", [limit])
        return [Checkin.model_validate(r["doc"]) for r in rows]

    async def put_alert(self, a: Alert) -> None:
        await self._put_doc("alerts", a.id, a, {"resident_id": a.resident_id, "ts": a.ts, "status": a.status})

    async def get_alert(self, alert_id: str) -> Alert | None:
        rows = await self._rows("SELECT doc FROM hearth.alerts WHERE id = %s", [alert_id])
        return Alert.model_validate(rows[0]["doc"]) if rows else None

    async def list_alerts(self, status: str | None = None) -> list[Alert]:
        if status:
            rows = await self._rows("SELECT doc FROM hearth.alerts WHERE status = %s ORDER BY ts DESC", [status])
        else:
            rows = await self._rows("SELECT doc FROM hearth.alerts ORDER BY ts DESC")
        return [Alert.model_validate(r["doc"]) for r in rows]

    async def delete_checkins_and_alerts(self) -> tuple[int, int]:
        a = await self._rows("WITH d AS (DELETE FROM hearth.checkins RETURNING 1) SELECT count(*) AS n FROM d")
        b = await self._rows("WITH d AS (DELETE FROM hearth.alerts RETURNING 1) SELECT count(*) AS n FROM d")
        return int(a[0]["n"]), int(b[0]["n"])

    # ------------------------------------------------------------------ push / kv
    async def add_push_subscription(self, sub: PushSubscription) -> None:
        await self._exec("INSERT INTO hearth.push_subscriptions (endpoint, doc) VALUES (%s, %s) "
                         "ON CONFLICT (endpoint) DO UPDATE SET doc = EXCLUDED.doc",
                         [sub.endpoint, Jsonb(sub.model_dump(mode="json"))])

    async def list_push_subscriptions(self) -> list[PushSubscription]:
        rows = await self._rows("SELECT doc FROM hearth.push_subscriptions")
        return [PushSubscription.model_validate(r["doc"]) for r in rows]

    async def kv_get(self, key: str) -> Any:
        rows = await self._rows("SELECT value FROM hearth.kv WHERE key = %s", [key])
        return rows[0]["value"] if rows else None

    async def kv_set(self, key: str, value: Any) -> None:
        await self._exec("INSERT INTO hearth.kv (key, value) VALUES (%s, %s) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", [key, Jsonb(value)])

    # ------------------------------------------------------------------ week (continuous aggregates)
    async def refresh_aggregates(self, start: datetime, end: datetime) -> None:
        async with self.pool.connection() as conn:
            for view in ("hearth.fluids_daily", "hearth.adherence_daily"):
                await conn.execute("CALL refresh_continuous_aggregate(%s, %s, %s)", [view, start, end])

    async def refresh_history(self, since: datetime) -> None:
        """Re-materialise complete past days after a write into them (never today: see day_stats)."""
        today = datetime.now(UTC).astimezone(ZoneInfo(CAGG_TZ)).date()
        today_start, _ = local_day_bounds(today, CAGG_TZ)
        start = since - timedelta(days=1)
        if start >= today_start - timedelta(days=1):
            return
        try:
            await self.refresh_aggregates(start, today_start)
        except Exception as e:  # noqa: BLE001
            log.warning("cagg refresh failed (%s); the policy will catch up", e)

    async def day_stats(self, days: list[date], tz: str) -> list[DayStats] | None:
        if tz != CAGG_TZ or not days:
            return None
        start, _ = local_day_bounds(min(days), tz)
        today_start, end = local_day_bounds(max(days), tz)
        if self.dirty:
            # Past days: rows written into already-materialised buckets (seed history, privacy
            # delete) need a refresh. Today is deliberately left out of the window, so its bucket
            # stays above the watermark and real-time aggregation serves it straight from the
            # hypertable: a drink confirmed a second ago is already in /api/week.
            try:
                await self.refresh_aggregates(start - timedelta(days=1), today_start)
                self.dirty = False
            except Exception as e:  # noqa: BLE001
                log.warning("cagg refresh failed (%s); serving what is materialised", e)
        f = await self._rows(
            "SELECT (day AT TIME ZONE %s)::date AS d, fluid_ml, drinks_confirmed, drinks_refused, checkins_missed "
            "FROM hearth.fluids_daily WHERE day >= %s AND day < %s", [tz, start, end])
        a = await self._rows(
            "SELECT (day AT TIME ZONE %s)::date AS d, meds_confirmed, meals_confirmed "
            "FROM hearth.adherence_daily WHERE day >= %s AND day < %s", [tz, start, end])
        fm = {r["d"]: r for r in f}
        am = {r["d"]: r for r in a}
        out = []
        for d in days:
            fr, ar = fm.get(d, {}), am.get(d, {})
            out.append(DayStats(
                day=d.isoformat(),
                fluid_ml=int(fr.get("fluid_ml") or 0),
                drinks_confirmed=int(fr.get("drinks_confirmed") or 0),
                drinks_refused=int(fr.get("drinks_refused") or 0),
                meals_confirmed=int(ar.get("meals_confirmed") or 0),
                meds_confirmed=int(ar.get("meds_confirmed") or 0),
                meds_expected=0,
                checkins_missed=int(fr.get("checkins_missed") or 0),
            ))
        return out
