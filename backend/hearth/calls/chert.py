"""Chert FaceTime Calls API client (BYOR outbound), behind a small interface for tests.

Shapes observed on our project, read-only, Sat 2026-11-14 23:45 ET:
  GET  /lines            -> {"data": {"items": [Line], "total", "limit", "offset"}, "request_id"}
  GET  /calls?limit=     -> {"data": {"items": [Call], ...}}  (no events in list items)
  GET  /calls/{id}       -> {"data": Call}  Call has status, terminal_reason, terminal_detail,
                            media_status, active_at, ended_at, duration_seconds and
                            facetime_call_events: [{event_type, source, sequence, payload, created_at}]
  GET  /calls/{id}/events-> {"data": {"call_id", "events": [...]}}
  POST /calls            -> 201 {"data": Call, "request_id"}; errors {"error": {...}} with 4xx
  POST /calls/{id}/end   -> 202
Statuses seen: allocated, dialing, active, ending, ended, failed. Events seen (source):
  allocated (control_plane), dialing / active / bridge_ready / agent_connected / ending / ended /
  failed / cleanup_complete (worker), end_requested (customer).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

TERMINAL = {
    "ended", "failed", "rejected", "declined", "cancelled", "canceled", "completed",
    "no_answer", "missed", "busy", "expired", "timeout",
}
NO_ANSWER_HINTS = {
    "no_answer", "unanswered", "not_answered", "declined", "rejected", "busy", "timeout",
    "ring_timeout", "missed", "recipient_unavailable", "callee_unavailable", "remote_declined",
    "caller_hung_up_before_answer",
}


@dataclass
class ChertResult:
    status: int
    body: dict[str, Any]

    @property
    def data(self) -> dict[str, Any]:
        d = self.body.get("data")
        return d if isinstance(d, dict) else {}

    def error_text(self) -> str:
        err = self.body.get("error")
        if isinstance(err, dict):
            code = err.get("code") or err.get("type") or ""
            msg = err.get("message") or err.get("detail") or ""
            return f"{code}: {msg}".strip(": ") or f"HTTP {self.status}"
        if isinstance(err, str):
            return err
        return str(self.body.get("detail") or self.body.get("message") or f"HTTP {self.status}")[:200]


class ChertApi(Protocol):
    async def list_lines(self) -> list[dict[str, Any]]: ...

    async def create_call(self, body: dict[str, Any], idempotency_key: str) -> ChertResult: ...

    async def get_call(self, call_id: str) -> dict[str, Any]:
        """`data` of GET /calls/{id}. Raises on transport errors."""
        ...

    async def get_events(self, call_id: str) -> list[dict[str, Any]]: ...

    async def end_call(self, call_id: str, idempotency_key: str) -> int:
        """Returns the HTTP status (202 expected)."""
        ...

    async def aclose(self) -> None: ...


def events_of(call: dict[str, Any]) -> list[dict[str, Any]]:
    ev = call.get("facetime_call_events")
    return ev if isinstance(ev, list) else []


def has_bridge_ready(events: list[dict[str, Any]]) -> bool:
    return any(
        (e.get("event_type") or e.get("type")) == "bridge_ready" and e.get("source") == "worker"
        for e in events
    )


def terminal_text(call: dict[str, Any]) -> str:
    st = call.get("status") or "?"
    reason = call.get("terminal_reason")
    detail = call.get("terminal_detail")
    bits = [b for b in (reason, detail) if b and b != "unknown"]
    return f"call {st}" + (f" ({', '.join(bits)})" if bits else "")


def looks_unanswered(call: dict[str, Any]) -> bool:
    vals = {str(call.get(k) or "").lower() for k in ("status", "terminal_reason", "terminal_detail")}
    return bool(vals & NO_ANSWER_HINTS)


class HttpChert:
    def __init__(self, base: str, api_key: str, project_id: str, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Chert-Project-Id": project_id,
                "User-Agent": "hearth-home/0.1",
            },
        )

    @staticmethod
    def _json(r: httpx.Response) -> dict[str, Any]:
        try:
            j = r.json()
            return j if isinstance(j, dict) else {"data": j}
        except ValueError:
            return {"error": {"code": f"http_{r.status_code}", "message": r.text[:200]}}

    async def list_lines(self) -> list[dict[str, Any]]:
        r = await self._client.get("/lines")
        r.raise_for_status()
        data = self._json(r).get("data")
        if isinstance(data, dict):
            return list(data.get("items") or [])
        return list(data or [])

    async def create_call(self, body: dict[str, Any], idempotency_key: str) -> ChertResult:
        r = await self._client.post("/calls", json=body, headers={"Idempotency-Key": idempotency_key})
        return ChertResult(r.status_code, self._json(r))

    async def get_call(self, call_id: str) -> dict[str, Any]:
        r = await self._client.get(f"/calls/{call_id}")
        r.raise_for_status()
        return ChertResult(r.status_code, self._json(r)).data

    async def get_events(self, call_id: str) -> list[dict[str, Any]]:
        r = await self._client.get(f"/calls/{call_id}/events")
        r.raise_for_status()
        data = self._json(r).get("data")
        if isinstance(data, dict):
            return list(data.get("events") or data.get("items") or [])
        return list(data or [])

    async def end_call(self, call_id: str, idempotency_key: str) -> int:
        r = await self._client.post(
            f"/calls/{call_id}/end", json={}, headers={"Idempotency-Key": idempotency_key}
        )
        return r.status_code

    async def aclose(self) -> None:
        await self._client.aclose()
