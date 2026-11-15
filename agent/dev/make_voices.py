"""Make test WAVs of "her" lines (synthetic test fixtures; nothing from a real call).

Tries the local Kokoro server first, then Gemini TTS.
    uv run python dev/make_voices.py
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import httpx

OUT = Path(__file__).parent / "audio"
LINES = {
    "refuse1": "No thank you, I'm not thirsty. I had tea earlier.",
    "refuse2": "No, really, dear. I don't want any just now.",
    "fell": "Help me, I fell and I can't get up.",
    "hello": "Oh, hello. Yes, I'm here.",
    "cup": "Here's my cup. Here it is.",
    "drinking": "There. I'm having a drink now.",
    "chat": "Oh, it's a lovely day. I was just looking at the garden. The roses are out, you know.",
}


def kokoro(text: str) -> bytes | None:
    try:
        r = httpx.post("http://host.docker.internal:8880/v1/audio/speech", timeout=20,
                       json={"model": "kokoro", "input": text, "voice": "bf_emma", "response_format": "wav"})
        r.raise_for_status()
        return r.content
    except Exception as e:  # noqa: BLE001
        print("kokoro unavailable:", type(e).__name__)
        return None


def gemini(text: str) -> bytes:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                          http_options=types.HttpOptions(api_version="v1beta"))
    resp = client.models.generate_content(
        model=os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts"),
        contents=f"Say slowly, like a gentle 81-year-old woman: {text}",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Gacrux"))
            ),
        ),
    )
    return resp.candidates[0].content.parts[0].inline_data.data  # PCM s16le 24 kHz mono


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, text in LINES.items():
        path = OUT / f"{name}.wav"
        if path.exists():
            continue
        data = kokoro(text)
        if data:
            path.write_bytes(data)
        else:
            pcm = gemini(text)
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
        print("wrote", path.name)


if __name__ == "__main__":
    main()
