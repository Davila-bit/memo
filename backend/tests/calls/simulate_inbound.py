"""No keys, sockets or real calls: uv run python tests/calls/simulate_inbound.py."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

# Direct script invocation puts tests/calls on sys.path rather than the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hearth.calls import HearthCallService
from hearth.core.app import create_app
from hearth.core.fallbacks import KeywordSearch, LogNotifier, RegexRedactor, TemplateDigest
from hearth.core.settings import Settings
from tests.calls.fakes import FAST, SETTINGS, FakeChert, FakeRooms, ev


async def simulate() -> None:
    rooms = FakeRooms()
    chert = FakeChert(
        [
            {"status": "active"},
            {"status": "active", "facetime_call_events": [ev("bridge_ready")]},
        ],
        rooms=rooms,
    )
    calls = HearthCallService(SETTINGS, rooms=rooms, chert=chert, timings=FAST)
    integrations = {
        "notifier": LogNotifier(),
        "search": KeywordSearch(),
        "digest": TemplateDigest(),
        "redactor": RegexRedactor(),
    }
    with (
        TemporaryDirectory(prefix="hearth-inbound-simulation-") as scratch,
        patch("hearth.core.app._build_calls", return_value=(calls, None)),
        patch("hearth.core.app._build_integrations", return_value=integrations),
    ):
        settings = Settings(
            chert_hook_key="simulation-only",
            scheduler=False,
            session_secret="simulation-only",
            media_dir=Path(scratch),
        )
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://simulation") as client,
        ):
            event = {
                "event_id": "93e49cee-057a-41c7-a81e-c42ce6f51cc3",
                "type": "call.incoming",
                "created_at": "2026-11-15T06:00:00Z",
                "data": {
                    "call_id": "09addbf9-e7a5-46b2-9da3-b3e5be753cf5",
                    "line_id": "line-1",
                    "media": {"audio": True, "video": True},
                    "from_handle_hash": None,
                    "occurred_at": "2026-11-15T06:00:00Z",
                },
            }
            before = time.monotonic()
            reply = await client.post("/hooks/chert/simulation-only", json=event)
            elapsed = time.monotonic() - before
            payload = reply.json()
            assert reply.status_code == 200 and payload["action"] == "accept"
            assert elapsed < 5
            print(f"HTTP {reply.status_code}; reply time: {elapsed * 1000:.1f} ms (budget 5000 ms)")
            print(json.dumps({**payload, "participant_token": "<redacted>"}, indent=2))
            duplicate = await client.post("/hooks/chert/simulation-only", json=event)
            assert duplicate.json() == payload
            svc = app.state.svc
            c = await svc.store.active_checkin()
            assert c is not None and c.trigger == "inbound"
            async with asyncio.timeout(2):
                while (await svc.store.get_checkin(c.id)).status != "connected":
                    await asyncio.sleep(0.01)
            print("Live: active + worker bridge_ready; room metadata hearth_live=true")
            await calls.end(c.id, "simulation complete")
            task = calls._calls[c.id].task
            await asyncio.wait_for(task, 2)
            final = await svc.store.get_checkin(c.id)
            assert final.status == "ended" and not chert.created and len(chert.ended) == 1
            assert ("delete_room", c.room) in rooms.log
            print("Duplicate reused decision; check-in ended; fake call, dispatch and room cleaned up")
            print("No real calls, network requests or media recording")


if __name__ == "__main__":
    asyncio.run(simulate())
