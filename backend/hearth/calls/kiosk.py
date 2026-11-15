"""GET /kiosk: the WebRTC kitchen screen (stands in for the iPad's FaceTime on an Android tablet/laptop)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@lru_cache(maxsize=1)
def kiosk_html() -> str:
    return (Path(__file__).parent / "kiosk.html").read_text(encoding="utf-8")


@router.get("/kiosk", response_class=HTMLResponse, include_in_schema=False)
async def kiosk_page() -> HTMLResponse:
    # The page reads ?key= and ?device= itself and calls /api/kiosk/token (served by the core).
    return HTMLResponse(
        kiosk_html(),
        headers={
            "Cache-Control": "no-store",
            "Permissions-Policy": "camera=(self), microphone=(self), screen-wake-lock=(self)",
            "Referrer-Policy": "no-referrer",  # the URL carries ?key=
        },
    )
