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
    n = await s.reindex(events())
    assert n == len(events())
    yield s
    await s.drop()


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
