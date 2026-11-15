"""Prove a Gemini Live session opens on our key. Prints model, time to first audio, bytes. No disk writes.

    uv run python dev/live_probe.py [model ...]
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from google import genai
from google.genai import types


async def probe(client: genai.Client, model: str) -> None:
    t0 = time.monotonic()
    cfg = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction="You are a brief, warm assistant.",
    )
    try:
        async with client.aio.live.connect(model=model, config=cfg) as s:
            t_open = time.monotonic() - t0
            await s.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text="Say hello in five words.")]),
                turn_complete=True,
            )
            first, nbytes = None, 0
            async for msg in s.receive():
                if msg.data:
                    first = first or time.monotonic() - t0
                    nbytes += len(msg.data)
                if msg.server_content and msg.server_content.turn_complete:
                    break
            print(f"OK   {model}: open {t_open:.2f}s, first audio {first:.2f}s, {nbytes} bytes")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {model}: {type(e).__name__}: {str(e)[:200]}")


async def main() -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY missing")
        return
    client = genai.Client(api_key=key, http_options=types.HttpOptions(api_version="v1beta"))
    models = sys.argv[1:] or [os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live")]
    for m in models:
        await asyncio.wait_for(probe(client, m), 30)


if __name__ == "__main__":
    asyncio.run(main())
