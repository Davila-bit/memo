"""Small shared helpers: env reading, status tracking, local time formatting."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("hearth.integrations")

Status = str  # "ok" | "missing_key" | "error" | "mock"

# Live status per integration, read by integrations.health(). Updated as calls succeed or fail.
STATUS: dict[str, Status] = {}

_warned: set[str] = set()


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip()


def warn_once(key: str, message: str) -> None:
    """Log one line per process for a missing key / fallback, never the value."""
    if key not in _warned:
        _warned.add(key)
        log.warning(message)


def set_status(name: str, status: Status) -> None:
    STATUS[name] = status


def local_tz() -> ZoneInfo:
    """The resident's timezone. The contract keeps it on Resident; integrations default to the demo one."""
    # TODO(contract): Search/DigestWriter don't receive Resident.timezone; read it from env for now.
    name = env("HEARTH_TIMEZONE", "America/New_York") or "America/New_York"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("America/New_York")


def to_local(ts: datetime) -> datetime:
    if ts.tzinfo is None:

        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(local_tz())


def time_label(ts: datetime) -> str:
    """'7:04 pm'"""
    t = to_local(ts)
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def daypart(ts: datetime) -> str:
    h = to_local(ts).hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 21:
        return "evening"
    return "night"


def day_str(ts: datetime) -> str:
    return to_local(ts).strftime("%Y-%m-%d")


def weekday(ts: datetime) -> str:
    return to_local(ts).strftime("%A")


def day_phrase(ts: datetime, now: datetime) -> str:
    """'today', 'yesterday', 'on Tuesday', 'on Nov 3'."""
    d = to_local(ts).date()
    n = to_local(now).date()
    delta = (n - d).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if 1 < delta < 7:
        return f"on {weekday(ts)}"
    return f"on {to_local(ts).strftime('%b')} {d.day}"


def gemini_key() -> str | None:
    return env("GEMINI_API_KEY")


def gemini_text_model() -> str:
    return env("GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite") or "gemini-3.5-flash-lite"
