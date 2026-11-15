"""One tiny async wrapper around google-genai for text calls (digest, query translation)."""

from __future__ import annotations

import asyncio

from ._common import gemini_key, gemini_text_model, log, set_status

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=gemini_key())
    return _client


async def generate(prompt: str, *, system: str, json_mode: bool = False, timeout_s: float = 15.0,
                   temperature: float = 0.2) -> str | None:
    """Return the model's text, or None on any failure (logged, never raised)."""
    if not gemini_key():
        return None
    try:
        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=400,
            response_mime_type="application/json" if json_mode else None,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )
        resp = await asyncio.wait_for(
            _get_client().aio.models.generate_content(model=gemini_text_model(), contents=prompt, config=cfg),
            timeout=timeout_s + 1,
        )
        set_status("gemini_text", "ok")
        return (resp.text or "").strip() or None
    except Exception as e:  # noqa: BLE001
        set_status("gemini_text", "error")
        log.warning("gemini text call failed: %s %s", type(e).__name__, str(e)[:200])
        return None
