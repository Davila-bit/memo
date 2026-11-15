"""Ask-the-log against real Typesense Cloud (+ Gemini NL model). Skipped without keys.

    fnox exec --only TYPESENSE_HOST,TYPESENSE_API_KEY,GEMINI_API_KEY -- \
      env WS_ENV=TYPESENSE_HOST,TYPESENSE_API_KEY,GEMINI_API_KEY scripts/ws \
      'cd backend && uv run pytest -q -s tests/integrations/test_search_live.py'
"""

from __future__ import annotations

import os
import secrets

import pytest
import pytest_asyncio
from typesense.exceptions import ObjectNotFound

from hearth.integrations.search import TypesenseSearch

from .fixture import NOW, TODAY_DRINKS, TODAY_ML, events

pytestmark = [
    pytest.mark.skipif(not (os.environ.get("TYPESENSE_HOST") and os.environ.get("TYPESENSE_API_KEY")),
                       reason="needs TYPESENSE_HOST and TYPESENSE_API_KEY"),
    pytest.mark.asyncio(loop_scope="module"),
]

QUESTIONS: list[tuple[str, list[str]]] = [
    ("did Mum take her evening pills on Tuesday?", ["Yes.", "7:04 pm on Tuesday"]),
    ("how much has she drunk today?", [f"About {TODAY_ML} ml so far today", f"{TODAY_DRINKS} drinks"]),
    ("when did she last refuse a drink?", ["2:10 pm yesterday"]),
    ("what did she say when she refused?", ["not thirsty"]),
    ("did she eat lunch yesterday?", ["Yes.", "lunch", "12:15 pm yesterday"]),
    ("any check-ins she missed this week?", ["2 missed check-ins", "Monday 4:00 pm", "Thursday 10:30 am"]),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def ts():
    name = f"hearth_events_test_{secrets.token_hex(4)}"
    s = TypesenseSearch(collection=name)
    try:
        n = await s.reindex(events())
        assert n == len(events())
        yield s
    finally:
        await s.drop()
        with pytest.raises(ObjectNotFound):
            await s.client.collections[name].retrieve()


@pytest.mark.parametrize("q,expect", QUESTIONS, ids=[q for q, _ in QUESTIONS])
async def test_question(ts: TypesenseSearch, q: str, expect: list[str]) -> None:
    ts.last_debug.clear()
    r = await ts.ask(q, NOW, recent=[])
    print(f"\nQ: {q}\nA: {r.answer}\n   engine={r.engine} how={ts.last_debug.get('how')} hits={len(r.hits)}"
          f"\n   nl={ts.last_debug.get('nl')}\n   gemini={ts.last_debug.get('gemini')}"
          f"\n   plan={ts.last_debug.get('plan')}")
    assert r.engine == "typesense"
    for piece in expect:
        assert piece in r.answer


async def test_index_single_and_synonyms(ts: TypesenseSearch) -> None:
    """Synonym set: a query for 'fluids' finds documents that only say water / juice."""
    res = await ts._run({"q": "fluids", "filter_by": "kind:=drink"})
    assert res["found"] > 0
    from hearth_contracts.models import HearthEvent

    e = HearthEvent(id="ev-new", ts=NOW, resident_id="margaret", kind="note", source="manual",
                    summary="Sarah's visit: had a cup of tea together")
    await ts.index(e)
    res = await ts._run({"q": "tea", "filter_by": "kind:=note"})
    assert any(h["document"]["id"] == "ev-new" for h in res["hits"])


async def test_demo_clock_moves_the_nl_prompt(ts: TypesenseSearch) -> None:
    """Same question, demo clock one day earlier: 'today' must now mean Wednesday (500 ml over 3 drinks)."""
    from datetime import timedelta

    r = await ts.ask("how much has she drunk today?", NOW - timedelta(days=1), recent=[])
    print(f"\nQ (clock = Wed 3:30 pm): how much has she drunk today?\nA: {r.answer}\n   how={ts.last_debug.get('how')}"
          f" nl={ts.last_debug.get('nl')}")
    assert "About 500 ml so far today, across 3 drinks." == r.answer


@pytest.mark.skipif(os.environ.get("TYPESENSE_HYBRID") != "1", reason="hybrid is opt-in")
async def test_live_related_meaning(ts: TypesenseSearch) -> None:
    from hearth_contracts.models import HearthEvent

    event = HearthEvent(id="upset", ts=NOW, resident_id="margaret", source="manual", kind="note",
                        summary="She said she was tired of being asked.")
    await ts.index(event)
    result = await ts.ask("did she seem upset?", NOW, [])
    assert result.engine == "typesense" and result.mode == "related"
    assert result.answer == "Here are the closest moments I found."
    assert any(hit.event.id == event.id for hit in result.hits)


@pytest.mark.skipif(os.environ.get("TYPESENSE_VOICE") != "1", reason="voice is opt-in")
async def test_live_voice_silence_returns_normal_response(ts: TypesenseSearch) -> None:
    from .test_search_features import silent_wav

    # No speech synthesiser is installed in the workspace. A normal silent response has an
    # empty transcription, which our backend converts to its specific "couldn't hear" error.
    with pytest.raises(ValueError, match="I couldn't hear a short question"):
        await ts.transcribe(silent_wav())
