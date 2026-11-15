"""CallService: LiveKit room + agent dispatch, Chert FaceTime, kiosk transport.

    from hearth.calls import build_call_service, router
    calls = build_call_service()          # ports.CallService for transports "facetime" and "kiosk"
    app.include_router(router)            # GET /kiosk
"""

from __future__ import annotations

import logging

from .config import CallSettings, Timings, timings_from_env
from .kiosk import router
from .service import CallsUnavailable, HearthCallService

log = logging.getLogger("hearth.calls")

__all__ = ["CallSettings", "CallsUnavailable", "HearthCallService", "Timings", "build_call_service", "router"]


def build_call_service() -> HearthCallService:
    """Reads env (contracts/API.md §3). Missing keys degrade with one log line each."""
    s = CallSettings.from_env()
    t = timings_from_env()
    rooms = None
    chert = None
    if s.livekit_ready:
        from .livekit_ops import LiveKitRoomOps

        assert s.livekit_url and s.livekit_api_key and s.livekit_api_secret
        rooms = LiveKitRoomOps(s.livekit_url, s.livekit_api_key, s.livekit_api_secret, http_timeout=t.http)
    else:
        log.warning("calls: LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing; only the mock transport works")
    if s.chert_ready and rooms is not None:
        from .chert import HttpChert

        assert s.chert_api_key and s.chert_project_id
        chert = HttpChert(s.chert_base, s.chert_api_key, s.chert_project_id, timeout=t.http)
    elif not s.chert_ready:
        log.warning("calls: CHERT_API_KEY / CHERT_PROJECT_ID / CHERT_LINE_ID / CHERT_IPAD_NUMBER missing; "
                    "FaceTime check-ins fall back to the kiosk")
    return HearthCallService(s, rooms=rooms, chert=chert, timings=t)
