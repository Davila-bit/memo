"""One tiny async wrapper around google-genai for text calls (digest, query translation)."""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class WatchClassification(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    person_visible: bool
    status: Literal["ok", "unusual", "concern"]
    note: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def visible_concern(self) -> WatchClassification:
        if self.status == "concern" and not self.person_visible:
            raise ValueError("An empty view cannot be a concern")
        return self

    @field_validator("note")
    @classmethod
    def short_note(cls, value: str) -> str:
        if not value.strip() or len(value.split()) > 10:
            raise ValueError("Expected one short note")
        return value.strip()


async def classify_watch_frame(jpeg: bytes) -> WatchClassification | None:
    """One ephemeral image; failures log metadata only, never SDK exception text or image data."""
    if not gemini_key():
        log.info("watch classification skipped: missing Gemini key")
        return None
    try:
        from google.genai import types

        response = await asyncio.wait_for(
            _get_client().aio.models.generate_content(
                model=gemini_text_model(),
                contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                          ("Describe only posture and place in neutral words. No identity, no appearance, "
                          "no medical judgement. Use concern only for a person on the floor, lying "
                          "somewhere that is not a bed or sofa, slumped, or clearly in distress. "
                          "An empty view is unusual, never concern. Return JSON only: "
                          '{"person_visible": boolean, "status": "ok"|"unusual"|"concern", '
                          '"note": "at most 10 words"}.')],
                config=types.GenerateContentConfig(
                    temperature=0, max_output_tokens=150, response_mime_type="application/json",
                    http_options=types.HttpOptions(timeout=8000))),
            timeout=8.0)
        return WatchClassification.model_validate_json(response.text or "")
    except Exception:  # noqa: BLE001
        log.warning("watch classification skipped: Gemini unavailable or invalid response")
        return None
