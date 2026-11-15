"""Interfaces between backend modules, so separate people can build each side at once.

`backend/hearth/core` owns Store, Bus, Clock and the check-in lifecycle (CheckinService).
`backend/hearth/calls` implements CallService (LiveKit room + agent dispatch + Chert / kiosk / mock).
`backend/hearth/integrations` implements Notifier, Search, DigestWriter and Redactor.

Every implementation must have a no-key fallback that logs one line and keeps the app running.
"""

from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable, Protocol

from .models import (
    AgentJob,
    Alert,
    AskResponse,
    Checkin,
    CheckinOutcome,
    Digest,
    HearthEvent,
    KioskToken,
    WsMessage,
)


class Clock(Protocol):
    def now(self) -> datetime:
        """Demo-clock time (timezone-aware, UTC). Use this everywhere instead of datetime.now()."""
        ...


class Bus(Protocol):
    async def publish(self, msg: WsMessage) -> None:
        """Fan a live message out to every connected /ws client."""
        ...


class CallCallbacks(Protocol):
    """What the call layer tells the core while a call runs. Implemented by CheckinService."""

    async def call_ringing(self, checkin_id: str) -> None: ...

    async def call_connected(self, checkin_id: str) -> None:
        """She answered and media is flowing (Chert `bridge_ready`, or the kiosk has joined)."""
        ...

    async def call_finished(self, checkin_id: str, outcome: CheckinOutcome | None, error: str | None) -> None:
        """The call is over. `outcome` is None when the agent already reported one through a tool.
        "no_answer" when nobody picked up, "error" for technical failures."""
        ...


class CallService(Protocol):
    """Places one check-in call on a transport and cleans up after it. One call at a time."""

    async def start(self, checkin: Checkin, job: AgentJob, cb: CallCallbacks) -> None:
        """Returns quickly; the call runs as a background task. Must enforce the 90 s budget:
        whatever happens, the call is ended and `cb.call_finished` is invoked exactly once."""
        ...

    async def end(self, checkin_id: str, reason: str) -> None:
        """Hang up now (agent called end_checkin, or the daughter cancelled). Idempotent."""
        ...

    async def kiosk_token(self, device: str) -> KioskToken:
        """LiveKit token for the kiosk page (room `kiosk-<device>`)."""
        ...

    def health(self) -> dict[str, str]:
        """{"livekit": "ok"|"missing_key"|..., "chert": ...}"""
        ...


class Notifier(Protocol):
    async def notify(self, title: str, body: str, *, urgent: bool = False, url: str | None = None,
                     actions: list[tuple[str, str]] | None = None) -> None:
        """Tell the daughter outside the app (Web Push, plus ntfy when urgent). Never raises."""
        ...

    async def alert(self, alert: Alert) -> None:
        """Convenience wrapper that formats an Alert, urgent when alert.tier == "urgent"."""
        ...


class Search(Protocol):
    async def index(self, event: HearthEvent) -> None: ...

    async def ask(self, q: str, now: datetime, recent: list[HearthEvent]) -> AskResponse:
        """Answer a natural-language question about the log. `recent` is the fallback corpus."""
        ...


class DigestWriter(Protocol):
    async def write(self, day: str, events: list[HearthEvent], her_name: str) -> Digest: ...


class Redactor(Protocol):
    def redact(self, text: str) -> str:
        """Strip names, phone numbers, addresses etc. from evidence / said text BEFORE storage."""
        ...


OnDue = Callable[[str], Awaitable[None]]  # schedule tick -> start check-in for schedule_item_id
