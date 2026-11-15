"""Thin client for the core's /internal agent endpoints (contracts/API.md §6).

Every call: header X-Hearth-Token, 3 s timeout, one retry. Never raises: on failure it logs one
line (endpoint + error class, never the body) and returns None, so the call carries on.
"""

from __future__ import annotations

import logging
import os

import httpx
from pydantic import BaseModel

from hearth_contracts import (
    AgentCaption,
    AgentStatus,
    Checkin,
    ToolConfirmDrink,
    ToolConfirmMeal,
    ToolConfirmMed,
    ToolEndCheckin,
    ToolFlagConcern,
    ToolReportRefusal,
    ToolResult,
)

log = logging.getLogger("hearth.api")

DEFAULT_TOKEN = "dev-internal"


def internal_token() -> str:
    tok = os.environ.get("HEARTH_INTERNAL_TOKEN")
    if not tok:
        log.warning("HEARTH_INTERNAL_TOKEN not set; using the dev default")
        return DEFAULT_TOKEN
    return tok


class HearthApi:
    def __init__(self, api_base: str, checkin_id: str, token: str | None = None,
                 timeout: float = 3.0, client: httpx.AsyncClient | None = None) -> None:
        self.base = f"{api_base.rstrip('/')}/internal/checkins/{checkin_id}"
        self._client = client or httpx.AsyncClient(
            timeout=timeout, headers={"X-Hearth-Token": token or internal_token()}
        )
        if client is not None:
            client.headers["X-Hearth-Token"] = token or internal_token()
        self.ended = False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: BaseModel) -> ToolResult | None:
        payload = body.model_dump(mode="json")
        for attempt in (1, 2):
            try:
                r = await self._client.post(f"{self.base}/{path}", json=payload)
                if r.status_code == 204 or not r.content:
                    return ToolResult()
                r.raise_for_status()
                return ToolResult.model_validate(r.json())
            except Exception as e:  # noqa: BLE001
                log.warning("POST %s failed (attempt %d): %s", path, attempt, type(e).__name__)
        return None

    async def status(self, status: str) -> ToolResult | None:
        return await self._post("status", AgentStatus(status=status))  # type: ignore[arg-type]

    async def caption(self, speaker: str, text: str, final: bool = True) -> None:
        text = text.strip()[:400]
        if text:
            await self._post("caption", AgentCaption(speaker=speaker, text=text, final=final))  # type: ignore[arg-type]

    async def confirm_drink(self, body: ToolConfirmDrink) -> ToolResult | None:
        return await self._post("confirm_drink", body)

    async def confirm_meal(self, body: ToolConfirmMeal) -> ToolResult | None:
        return await self._post("confirm_meal", body)

    async def confirm_med(self, body: ToolConfirmMed) -> ToolResult | None:
        return await self._post("confirm_med", body)

    async def report_refusal(self, body: ToolReportRefusal) -> ToolResult | None:
        return await self._post("report_refusal", body)

    async def flag_concern(self, body: ToolFlagConcern) -> ToolResult | None:
        return await self._post("flag_concern", body)

    async def end(self, reason: str, detail: str = "") -> ToolResult | None:
        """Idempotent on our side: only the first end is sent."""
        if self.ended:
            return None
        self.ended = True
        return await self._post("end", ToolEndCheckin(reason=reason, detail=detail[:200]))  # type: ignore[arg-type]

    async def get_checkin(self) -> Checkin | None:
        try:
            r = await self._client.get(self.base)
            r.raise_for_status()
            return Checkin.model_validate(r.json())
        except Exception as e:  # noqa: BLE001
            log.debug("GET checkin failed: %s", type(e).__name__)
            return None
