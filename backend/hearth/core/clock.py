"""Demo clock and the live bus."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from hearth_contracts import Clock, WsClock

from .store import Store

log = logging.getLogger("hearth.core")


class DemoClock:
    """Real UTC time plus a persisted offset in minutes. `now()` is sync (ports.Clock)."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.offset_minutes = 0

    async def load(self) -> None:
        v = await self.store.kv_get("clock_offset")
        self.offset_minutes = int(v or 0)

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(minutes=self.offset_minutes)

    async def set_offset(self, minutes: int) -> None:
        self.offset_minutes = int(minutes)
        await self.store.kv_set("clock_offset", self.offset_minutes)

    def model(self) -> Clock:
        return Clock(now=self.now(), offset_minutes=self.offset_minutes)

    def ws(self) -> WsClock:
        return WsClock(now=self.now(), offset_minutes=self.offset_minutes)


def local_now(clock: DemoClock, tz: str) -> datetime:
    return clock.now().astimezone(ZoneInfo(tz))


def local_day_bounds(d: date, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start = datetime.combine(d, time(0, 0), tzinfo=z)
    end = datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=z)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def at_local(d: date, hhmm: str, tz: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(d, time(h, m), tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def in_quiet_hours(hhmm: str, quiet: tuple[str, str]) -> bool:
    start, end = quiet
    if start == end:
        return False
    if start < end:
        return start <= hhmm < end
    return hhmm >= start or hhmm < end


def fmt_time(dt: datetime, tz: str) -> str:
    """'2:41 pm'"""
    t = dt.astimezone(ZoneInfo(tz))
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def fmt_short(dt: datetime, tz: str) -> str:
    """'2:00' / '2:30'"""
    t = dt.astimezone(ZoneInfo(tz))
    return f"{t.hour % 12 or 12}:{t.minute:02d}"


class Bus:
    """In-process fan-out to /ws clients. Slow clients drop messages rather than block."""

    def __init__(self) -> None:
        self.queues: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self.queues.discard(q)

    async def publish(self, msg) -> None:  # ports.Bus
        data = msg.model_dump_json()
        for q in list(self.queues):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                log.warning("ws client is too slow; dropped one message")
