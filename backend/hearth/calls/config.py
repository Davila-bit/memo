"""Settings and timeouts for the call layer. Values come from the environment (contracts/API.md §3)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

CHERT_BASE = "https://facetime.trychert.com/api/v1"


def _env(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Timings:
    """Every wait in the call layer. Nothing waits forever. Tests shrink these."""

    agent_join: float = 20.0  # agent participant with 1 unmuted audio + 1 video track
    ring: float = 45.0  # HEARTH_RING_SECONDS: from Chert 201 until live
    poll: float = 0.5  # Chert GET /calls/{id} and LiveKit list_participants while waiting
    live_poll: float = 1.0  # list_participants while live
    kiosk_presence: float = 20.0  # kiosk participant must be in its room
    kiosk_answer: float = 20.0  # kiosk auto-answers (unmutes) ~3 s after the agent joins
    ending_grace: float = 15.0  # a call in `ending` before live must go terminal within this
    end_wait: float = 15.0  # after POST /end (202), poll until terminal
    http: float = 10.0  # each Chert / LiveKit HTTP request
    cleanup: float = 25.0  # LiveKit part of cleanup (metadata, agent removal, dispatch, room)
    watchdog_extra: float = 60.0  # hard cap = agent_join + ring + max_seconds + this


@dataclass(frozen=True)
class CallSettings:
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    chert_api_key: str | None = None
    chert_project_id: str | None = None
    chert_line_id: str | None = None
    chert_ipad_number: str | None = None
    agent_name: str = "hearth-agent"
    kiosk_device: str = "kitchen"
    chert_base: str = CHERT_BASE
    skip_line_check: bool = False  # HEARTH_CHERT_SKIP_LINE_CHECK=1 if our readiness gate is ever wrong

    @classmethod
    def from_env(cls) -> CallSettings:
        return cls(
            livekit_url=_env("LIVEKIT_URL"),
            livekit_api_key=_env("LIVEKIT_API_KEY"),
            livekit_api_secret=_env("LIVEKIT_API_SECRET"),
            chert_api_key=_env("CHERT_API_KEY"),
            chert_project_id=_env("CHERT_PROJECT_ID"),
            chert_line_id=_env("CHERT_LINE_ID"),
            chert_ipad_number=_env("CHERT_IPAD_NUMBER"),
            agent_name=_env("HEARTH_AGENT_NAME") or "hearth-agent",
            kiosk_device=_env("HEARTH_KIOSK_DEVICE") or "kitchen",
            skip_line_check=os.environ.get("HEARTH_CHERT_SKIP_LINE_CHECK") == "1",
        )

    @property
    def livekit_ready(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)

    @property
    def chert_ready(self) -> bool:
        return bool(self.chert_api_key and self.chert_project_id and self.chert_line_id)


def timings_from_env() -> Timings:
    return Timings(ring=_float_env("HEARTH_RING_SECONDS", 45.0))


def to_e164(raw: str, default_country: str = "1") -> str:
    """'(404) 555-0123' -> '+14045550123'. A bare number gets 403 recipient_not_verified from Chert."""
    s = raw.strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        raise ValueError("phone number has no digits")
    if plus:
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    if len(digits) == 10:
        return f"+{default_country}{digits}"
    if len(digits) == 11 and digits.startswith(default_country):
        return "+" + digits
    return "+" + digits


def mask_number(raw: str | None) -> str:
    if not raw:
        return "(none)"
    digits = re.sub(r"\D", "", raw)
    return f"***{digits[-2:]}" if len(digits) >= 2 else "***"
