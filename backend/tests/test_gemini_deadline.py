"""Gemini rejects a server deadline under 10 s with 400 INVALID_ARGUMENT, which silently broke watch mode
and the Ask-box question reading. Every call must ask for at least 10 s."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from hearth.integrations import gemini


class _Models:
    def __init__(self, seen: list) -> None:
        self.seen = seen

    async def generate_content(self, *, model, contents, config):
        self.seen.append(config.http_options.timeout)
        return SimpleNamespace(text='{"person_visible": true, "status": "ok", "note": "sitting at the table"}')


def test_every_call_asks_for_at_least_ten_seconds(monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(gemini, "gemini_key", lambda: "k")
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(aio=SimpleNamespace(models=_Models(seen))))
    assert asyncio.run(gemini.generate("q", system="s", json_mode=True, timeout_s=8)) is not None
    assert asyncio.run(gemini.classify_watch_frame(b"\xff\xd8x")) is not None
    assert seen and min(seen) >= 10_000
