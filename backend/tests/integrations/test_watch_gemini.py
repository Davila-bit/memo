"""Watch classifier failures never expose images or model text in logs."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hearth.integrations import gemini


@pytest.mark.parametrize("text", [
    'not JSON',
    '{"person_visible": false, "status": "concern", "note": "Out of view"}',
    '{"person_visible": "yes", "status": "ok", "note": "At the table"}',
    '{"person_visible": true, "status": "fine", "note": "At the table"}',
    '{"person_visible": true, "status": "ok", "note": "one two three four five six seven eight nine ten eleven"}',
    '{"person_visible": true, "status": "ok", "note": ""}',
])
async def test_invalid_watch_result_stores_nothing(monkeypatch, caplog, text):
    monkeypatch.setattr(gemini, "gemini_key", lambda: "test-only")
    generate = AsyncMock(return_value=SimpleNamespace(text=text))
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))))
    assert await gemini.classify_watch_frame(b"private-image-bytes") is None
    assert "private-image-bytes" not in caplog.text and text not in caplog.text


async def test_missing_watch_key_logs_once_and_never_calls_model(monkeypatch, caplog):
    monkeypatch.setattr(gemini, "gemini_key", lambda: None)
    with caplog.at_level("INFO"):
        assert await gemini.classify_watch_frame(b"private-image-bytes") is None
    assert "watch classification skipped: missing Gemini key" in caplog.text
    assert "private-image-bytes" not in caplog.text


async def test_watch_timeout_degrades_and_uses_eight_seconds(monkeypatch):
    monkeypatch.setattr(gemini, "gemini_key", lambda: "test-only")
    generate = AsyncMock()
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))))

    async def timeout(coro, *, timeout):
        assert timeout == 8.0
        coro.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout)
    assert await gemini.classify_watch_frame(b"private-image-bytes") is None


async def test_watch_valid_result(monkeypatch):
    monkeypatch.setattr(gemini, "gemini_key", lambda: "test-only")
    generate = AsyncMock(return_value=SimpleNamespace(
        text='{"person_visible": true, "status": "ok", "note": "Sitting at the table"}'))
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))))
    result = await gemini.classify_watch_frame(b"private-image-bytes")
    assert result is not None and result.note == "Sitting at the table"
    assert generate.call_args.kwargs["model"] == gemini.gemini_text_model()
