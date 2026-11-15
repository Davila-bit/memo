"""Process-local kitchen presence and opt-in automatic call routing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from hearth_contracts import Route
from pydantic import BaseModel, Field

from .clock import DemoClock
from .settings import Settings

log = logging.getLogger("hearth.core")
Platform = Literal["ios", "android", "other"]


class KioskHeartbeat(BaseModel):
    device: str = Field(min_length=1, max_length=100)
    platform: Platform
    visible: bool


@dataclass(frozen=True)
class Heartbeat:
    at: datetime
    platform: Platform
    visible: bool


class RouteService:
    def __init__(self, clock: DemoClock, settings: Settings, real_calls: Any | None) -> None:
        self.clock = clock
        self.settings = settings
        self.real_calls = real_calls
        self.last: Heartbeat | None = None

    def heartbeat(self, body: KioskHeartbeat) -> None:
        if body.device.lower() == self.settings.kiosk_device.lower():
            self.last = Heartbeat(self.clock.now(), body.platform, body.visible)

    def decide(self) -> Route:
        last = self.last
        online = bool(last and last.visible and timedelta(0) <= self.clock.now() - last.at < timedelta(seconds=25))
        platform = last.platform if last else None
        if online:
            return Route(transport="kiosk", reason="kitchen screen is up", kitchen_online=True, platform=platform)
        if self.settings.auto_facetime and platform in ("ios", None) and self.real_calls is not None:
            try:
                health = self.real_calls.health() or {}
                if health.get("livekit") == "ok" and health.get("chert") == "ok":
                    return Route(transport="facetime", reason="kitchen screen is not open; ringing FaceTime",
                                 kitchen_online=False, platform=platform)
            except Exception as e:  # noqa: BLE001
                log.warning("route: call service health failed (%s)", type(e).__name__)
        return Route(transport="kiosk", reason="kitchen screen is not open; waiting for it",
                     kitchen_online=False, platform=platform)
