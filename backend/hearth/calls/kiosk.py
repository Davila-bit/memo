"""GET /kiosk: the WebRTC kitchen screen (stands in for the iPad's FaceTime on an Android tablet/laptop)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@lru_cache(maxsize=1)
def kiosk_html() -> str:
    return (Path(__file__).parent / "kiosk.html").read_text(encoding="utf-8")


@router.get("/kiosk", response_class=HTMLResponse, include_in_schema=False)
async def kiosk_page(request: Request) -> HTMLResponse:
    # The page reads ?key= and ?device= itself and calls /api/kiosk/token (served by the core).
    enabled = getattr(getattr(request.app.state, "settings", None), "watch", False)
    return HTMLResponse(
        kiosk_html().replace("/* HEARTH_WATCH */ false",
                             "true" if enabled else "false"),
        headers={
            "Cache-Control": "no-store",
            "Permissions-Policy": "camera=(self), microphone=(self), screen-wake-lock=(self)",
            "Referrer-Policy": "no-referrer",  # the URL carries ?key=
        },
    )
