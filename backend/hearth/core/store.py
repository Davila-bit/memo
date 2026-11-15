"""Store: one interface, two implementations (MemoryStore here, PgStore in pgstore.py).

Both behave the same. Events are append-only (except the privacy delete). Everything else is
upserted whole. `HearthEvent.for_kind` identifies what a refusal or missed check-in was for.
"""

from __future__ import annotations

import copy
from datetime import date, datetime
from typing import Any, Protocol

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


class Store(Protocol):
    name: str  # "memory" | "tiger"

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def is_empty(self) -> bool: ...
    async def wipe(self) -> None:
        """Delete everything except push subscriptions and kv["clock_offset"]."""
        ...

    # resident / schedule / cues
    async def get_resident(self) -> Resident | None: ...
    async def put_resident(self, r: Resident) -> None: ...
    async def list_schedule(self) -> list[ScheduleItem]: ...
    async def get_schedule_item(self, item_id: str) -> ScheduleItem | None: ...
    async def put_schedule_item(self, item: ScheduleItem) -> None: ...
    async def delete_schedule_item(self, item_id: str) -> bool: ...
    async def list_cues(self) -> list[Cue]: ...
    async def get_cue(self, cue_id: str) -> Cue | None: ...
    async def put_cue(self, cue: Cue) -> None: ...

    # events
    async def add_event(self, ev: HearthEvent) -> None: ...
    async def add_events(self, evs: list[HearthEvent]) -> None: ...
    async def list_events(self, *, since: datetime | None = None, until: datetime | None = None,
                          kind: str | None = None, limit: int = 100) -> list[HearthEvent]:
        """Newest first."""
        ...
    async def count_events(self) -> int: ...
    async def delete_all_events(self) -> int: ...

    # check-ins / alerts
    async def put_checkin(self, c: Checkin) -> None: ...
    async def get_checkin(self, checkin_id: str) -> Checkin | None: ...
    async def active_checkin(self) -> Checkin | None: ...
    async def list_checkins(self, limit: int = 50) -> list[Checkin]: ...
    async def put_alert(self, a: Alert) -> None: ...
    async def get_alert(self, alert_id: str) -> Alert | None: ...
    async def list_alerts(self, status: str | None = None) -> list[Alert]: ...
    async def delete_checkins_and_alerts(self) -> tuple[int, int]: ...

    # push / kv
    async def add_push_subscription(self, sub: PushSubscription) -> None: ...
    async def list_push_subscriptions(self) -> list[PushSubscription]: ...
    async def kv_get(self, key: str) -> Any: ...
    async def kv_set(self, key: str, value: Any) -> None: ...

    # week
    async def day_stats(self, days: list[date], tz: str) -> list[DayStats] | None:
        """Per-day stats from the database (Tiger continuous aggregates). None = not supported here;
        the caller computes them in Python from events. meds_expected is filled by the caller."""
        ...


class MemoryStore:
    name = "memory"

    def __init__(self) -> None:
        self.resident: Resident | None = None
        self.schedule: dict[str, ScheduleItem] = {}
        self.cues: dict[str, Cue] = {}
        self.events: list[HearthEvent] = []
        self.checkins: dict[str, Checkin] = {}
        self.alerts: dict[str, Alert] = {}
        self.push: dict[str, PushSubscription] = {}
        self.kv: dict[str, Any] = {}

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def is_empty(self) -> bool:
        return self.resident is None

    async def wipe(self) -> None:
        offset = self.kv.get("clock_offset")
        self.resident = None
        self.schedule.clear()
        self.cues.clear()
        self.events.clear()
        self.checkins.clear()
        self.alerts.clear()
        self.kv.clear()
        if offset is not None:
            self.kv["clock_offset"] = offset

    async def get_resident(self) -> Resident | None:
        return self.resident.model_copy(deep=True) if self.resident else None

    async def put_resident(self, r: Resident) -> None:
        self.resident = r.model_copy(deep=True)

    async def list_schedule(self) -> list[ScheduleItem]:
        return sorted((i.model_copy() for i in self.schedule.values()), key=lambda i: (i.time, i.id))

    async def get_schedule_item(self, item_id: str) -> ScheduleItem | None:
        i = self.schedule.get(item_id)
        return i.model_copy() if i else None

    async def put_schedule_item(self, item: ScheduleItem) -> None:
        self.schedule[item.id] = item.model_copy()

    async def delete_schedule_item(self, item_id: str) -> bool:
        return self.schedule.pop(item_id, None) is not None

    async def list_cues(self) -> list[Cue]:
        return sorted((c.model_copy() for c in self.cues.values()), key=lambda c: c.id)

    async def get_cue(self, cue_id: str) -> Cue | None:
        c = self.cues.get(cue_id)
        return c.model_copy() if c else None

    async def put_cue(self, cue: Cue) -> None:
        self.cues[cue.id] = cue.model_copy()

    async def add_event(self, ev: HearthEvent) -> None:
        self.events.append(ev.model_copy(deep=True))

    async def add_events(self, evs: list[HearthEvent]) -> None:
        for ev in evs:
            await self.add_event(ev)

    async def list_events(self, *, since: datetime | None = None, until: datetime | None = None,
                          kind: str | None = None, limit: int = 100) -> list[HearthEvent]:
        out = [e for e in self.events
               if (since is None or e.ts >= since) and (until is None or e.ts < until)
               and (kind is None or e.kind == kind)]
        out.sort(key=lambda e: (e.ts, e.id), reverse=True)
        return [e.model_copy(deep=True) for e in out[:limit]]

    async def count_events(self) -> int:
        return len(self.events)

    async def delete_all_events(self) -> int:
        n = len(self.events)
        self.events.clear()
        return n

    async def put_checkin(self, c: Checkin) -> None:
        self.checkins[c.id] = c.model_copy()

    async def get_checkin(self, checkin_id: str) -> Checkin | None:
        c = self.checkins.get(checkin_id)
        return c.model_copy() if c else None

    async def active_checkin(self) -> Checkin | None:
        act = [c for c in self.checkins.values() if c.status != "ended"]
        act.sort(key=lambda c: c.created_at, reverse=True)
        return act[0].model_copy() if act else None

    async def list_checkins(self, limit: int = 50) -> list[Checkin]:
        cs = sorted(self.checkins.values(), key=lambda c: c.created_at, reverse=True)
        return [c.model_copy() for c in cs[:limit]]

    async def put_alert(self, a: Alert) -> None:
        self.alerts[a.id] = a.model_copy()

    async def get_alert(self, alert_id: str) -> Alert | None:
        a = self.alerts.get(alert_id)
        return a.model_copy() if a else None

    async def list_alerts(self, status: str | None = None) -> list[Alert]:
        al = [a for a in self.alerts.values() if status is None or a.status == status]
        al.sort(key=lambda a: a.ts, reverse=True)
        return [a.model_copy() for a in al]

    async def delete_checkins_and_alerts(self) -> tuple[int, int]:
        n = (len(self.checkins), len(self.alerts))
        self.checkins.clear()
        self.alerts.clear()
        return n

    async def add_push_subscription(self, sub: PushSubscription) -> None:
        self.push[sub.endpoint] = sub.model_copy()

    async def list_push_subscriptions(self) -> list[PushSubscription]:
        return [s.model_copy() for s in self.push.values()]

    async def kv_get(self, key: str) -> Any:
        return copy.deepcopy(self.kv.get(key))

    async def kv_set(self, key: str, value: Any) -> None:
        self.kv[key] = copy.deepcopy(value)

    async def day_stats(self, days: list[date], tz: str) -> list[DayStats] | None:
        return None
