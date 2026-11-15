"""Live checks: Presidio on realistic sentences (local, always runs), Gemini digest (needs GEMINI_API_KEY),
one real ntfy publish to a random throwaway topic (needs HEARTH_LIVE_NTFY=1). Run with -s to see output."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time

import httpx
import pytest
from hearth_contracts.models import Alert

from hearth.integrations.digest import GeminiDigestWriter
from hearth.integrations.notify import HearthNotifier
from hearth.integrations.redact import PresidioRedactor

from .fixture import NOW, events

SENTENCES: list[tuple[str, list[str], list[str]]] = [
    # (before, must survive, must be gone)
    ("Held up the blue mug, drank twice, mug lower after", ["blue mug", "drank twice"], []),
    ("not thirsty, had tea with Mrs Patel next door", ["not thirsty", "tea", "next door"], ["Patel"]),
    ("Said Sarah phones her every Sunday at 404-555-0187", ["Sunday"], ["Sarah", "0187"]),
    ("Told me she lives at 12 Oak Tree Lane, Decatur, and her email is maggie.ellis@example.com",
     ["she lives at"], ["Oak Tree", "maggie", "Decatur"]),
    ("She said she fell in the kitchen and John from church is coming at 3", ["fell in the kitchen", "church"],
     ["John"]),
    ("Margaret's pill box was by the toaster; she asked if Dr Okafor rang on Tuesday",
     ["pill box", "toaster", "Tuesday"], ["Margaret", "Okafor"]),
]


def test_presidio_realistic_sentences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEARTH_REDACT_NAMES", "Margaret,Sarah,Ellis")
    t0 = time.perf_counter()
    r = PresidioRedactor()
    assert r.wait_ready(120), "presidio did not load"
    print(f"\npresidio cold start {time.perf_counter() - t0:.2f}s")
    slowest = 0.0
    for before, keep, gone in SENTENCES:
        t = time.perf_counter()
        after = r.redact(before)
        ms = (time.perf_counter() - t) * 1000
        slowest = max(slowest, ms)
        print(f"{ms:5.1f} ms  {before}\n          -> {after}")
        for k in keep:
            assert k in after, (k, after)
        for g in gone:
            assert g not in after, (g, after)
    assert slowest < 250  # ~5 ms on a workstation; shared CI runners spike


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="needs GEMINI_API_KEY")
async def test_digest_live() -> None:
    day = [e for e in events() if e.ts.astimezone(NOW.tzinfo).strftime("%d") in ("11", "12")]
    d = await GeminiDigestWriter().write("2026-11-11", day, "Mum")
    print(f"\ndigest ({d.generated_by}): {d.text}")
    assert d.text and "!" not in d.text


@pytest.mark.skipif(os.environ.get("HEARTH_LIVE_NTFY") != "1", reason="set HEARTH_LIVE_NTFY=1")
async def test_ntfy_live(monkeypatch: pytest.MonkeyPatch) -> None:
    topic = f"hearth-test-{secrets.token_urlsafe(18)}"  # throwaway; never printed
    monkeypatch.setenv("NTFY_TOPIC", topic)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)

    async def no_subs() -> list:
        return []

    n = HearthNotifier(no_subs)
    alert = Alert(id="al-live", ts=NOW, resident_id="margaret", kind="concern", tier="urgent",
                  title="Mum may need you — “check now”", detail="", said="I fell down")
    await n.alert(alert)
    base = n.ntfy_url
    msgs: list[dict] = []
    async with httpx.AsyncClient(timeout=10) as c:
        for _ in range(10):  # ntfy.sh writes its message cache in batches (~1 s)
            await asyncio.sleep(1)
            r = await c.get(f"{base}/{topic}/json", params={"poll": "1", "since": "all"})
            msgs = [m for m in (json.loads(line) for line in r.text.splitlines() if line.strip())
                    if m.get("event") == "message"]
            if msgs:
                break
    assert msgs, "ntfy poll returned no message"
    m = msgs[-1]
    print(f"\nntfy poll: title={m.get('title')!r} priority={m.get('priority')} tags={m.get('tags')} "
          f"actions={[a.get('label') for a in m.get('actions') or []]} click_set={bool(m.get('click'))}"
          f"\n  message={m.get('message')!r}")
    assert m["title"] == alert.title and m["priority"] == 5
    assert [a["label"] for a in m["actions"]] == ["I'm handling it", "Call 911"]
