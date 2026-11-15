"""Stub of the core's /internal endpoints, in memory, for agent tests (port 8103).

Prints what it receives: endpoint, kind and sizes. Never prints caption, evidence or said text.

    uv run uvicorn dev.stub_api:app --port 8103
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException, Response
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

app = FastAPI(title="hearth internal stub")
T0 = time.monotonic()
LOG: list[dict] = []
CHECKINS: dict[str, dict] = {}
TOKEN = "dev-internal"


def _auth(tok: str | None) -> None:
    if tok != TOKEN:
        raise HTTPException(401, "bad token")


def _rec(cid: str, name: str, **info) -> None:
    entry = {"t": round(time.monotonic() - T0, 2), "checkin": cid, "call": name, **info}
    LOG.append(entry)
    print("STUB", entry, flush=True)


def _state(cid: str) -> dict:
    return CHECKINS.setdefault(cid, {"status": "ringing", "outcome": None})


def _sizes(body: BaseModel, *text_fields: str) -> dict:
    d = body.model_dump()
    out = {k: v for k, v in d.items() if k not in text_fields}
    for f in text_fields:
        out[f"{f}_chars"] = len(d.get(f) or "")
    return out


@app.post("/internal/checkins/{cid}/status")
def status(cid: str, body: AgentStatus, x_hearth_token: str | None = Header(None)) -> ToolResult:
    _auth(x_hearth_token)
    _state(cid)["status"] = body.status
    _rec(cid, "status", status=body.status)
    return ToolResult()


@app.post("/internal/checkins/{cid}/caption", status_code=204)
def caption(cid: str, body: AgentCaption, x_hearth_token: str | None = Header(None)) -> Response:
    _auth(x_hearth_token)
    _rec(cid, "caption", **_sizes(body, "text"))
    return Response(status_code=204)


def _outcome(cid: str, outcome: str) -> bool:
    st = _state(cid)
    if st["outcome"] is None:
        st["outcome"] = outcome
        return True
    return False


def _confirm(cid: str, name: str, body: BaseModel) -> ToolResult:
    conf = body.confidence  # type: ignore[attr-defined]
    if conf < 0.5:
        _rec(cid, name, stored_as="note", **_sizes(body, "evidence"))
        return ToolResult(ok=True, event_id="note-1", say="I'm not quite sure I saw that. Could you ask her once more, kindly?")
    new = _outcome(cid, "confirmed")
    _rec(cid, name, stored_as="confirmed" if new else "ignored(outcome set)", **_sizes(body, "evidence"))
    return ToolResult(ok=True, event_id="ev-1")


@app.post("/internal/checkins/{cid}/confirm_drink")
def confirm_drink(cid: str, body: ToolConfirmDrink, x_hearth_token: str | None = Header(None)) -> ToolResult:
    _auth(x_hearth_token)
    return _confirm(cid, "confirm_drink", body)


@app.post("/internal/checkins/{cid}/confirm_meal")
def confirm_meal(cid: str, body: ToolConfirmMeal, x_hearth_token: str | None = Header(None)) -> ToolResult:
    _auth(x_hearth_token)
    return _confirm(cid, "confirm_meal", body)


@app.post("/internal/checkins/{cid}/confirm_med")
def confirm_med(cid: str, body: ToolConfirmMed, x_hearth_token: str | None = Header(None)) -> ToolResult:
    _auth(x_hearth_token)
    return _confirm(cid, "confirm_med", body)


@app.post("/internal/checkins/{cid}/report_refusal")
def report_refusal(cid: str, body: ToolReportRefusal, x_hearth_token: str | None = Header(None)) -> ToolResult:
    _auth(x_hearth_token)
    _outcome(cid, "refused")
    _rec(cid, "report_refusal", **_sizes(body, "said"))
    return ToolResult(ok=True, event_id="ev-r")


@app.post("/internal/checkins/{cid}/flag_concern")
def flag_concern(cid: str, body: ToolFlagConcern, x_hearth_token: str | None = Header(None)) -> ToolResult:
    _auth(x_hearth_token)
    _outcome(cid, "concern")
    _rec(cid, "flag_concern", **_sizes(body, "evidence_quote"))
    return ToolResult(ok=True, event_id="ev-c")


@app.post("/internal/checkins/{cid}/end")
def end(cid: str, body: ToolEndCheckin, x_hearth_token: str | None = Header(None)) -> ToolResult:
    _auth(x_hearth_token)
    _state(cid)["status"] = "ended"
    _rec(cid, "end", reason=body.reason, detail_chars=len(body.detail))
    return ToolResult()


@app.get("/internal/checkins/{cid}")
def get_checkin(cid: str, x_hearth_token: str | None = Header(None)) -> Checkin:
    _auth(x_hearth_token)
    st = _state(cid)
    return Checkin(id=cid, resident_id="margaret", kind="drink", transport="kiosk", status=st["status"],
                   outcome=st["outcome"], room=f"agent-test-{cid}", created_at=datetime.now(UTC))


@app.post("/stub/live/{cid}")
def set_live(cid: str) -> dict:
    """Test helper: make GET /internal/checkins/{id} say connected (the agent's fallback signal)."""
    _state(cid)["status"] = "connected"
    return {"ok": True}


@app.get("/stub/log")
def get_log() -> list[dict]:
    return LOG
