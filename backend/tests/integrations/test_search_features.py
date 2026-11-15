"""Typesense feature routing and schema upgrades, without keys or network."""

import base64
import io
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typesense.exceptions import ObjectNotFound

from hearth.integrations.search import VOICE_MODEL, TypesenseSearch, event_to_doc, schema

from .fixture import NOW, TODAY_ML, events


def fake_search(*, fields=None, missing=False, hybrid=True, voice=True):
    docs = SimpleNamespace(search=AsyncMock(return_value={"hits": []}),
                           import_=AsyncMock(return_value=[{"success": True}]))
    collection = SimpleNamespace(
        retrieve=AsyncMock(return_value={"fields": fields if fields is not None else schema("test", "syn", hybrid=True)["fields"],
                                         "voice_query_model": VOICE_MODEL.copy()}),
        update=AsyncMock(), delete=AsyncMock(), documents=docs,
    )
    if missing:
        collection.retrieve.side_effect = ObjectNotFound("missing")
    collections = {"test": collection}

    class Collections(dict):
        create = AsyncMock()

    client = SimpleNamespace(collections=Collections(collections),
                             synonym_sets={"syn": SimpleNamespace(upsert=AsyncMock())},
                             multi_search=SimpleNamespace(perform=AsyncMock()))
    search = TypesenseSearch(collection="test", synonym_set="syn", use_nl=False, hybrid=hybrid, voice=voice)
    search._client = client
    return search, collection


@pytest.fixture(autouse=True)
def no_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


async def test_unknown_question_uses_hybrid_and_preserves_relevance():
    search, col = fake_search()
    rows = [events()[0], events()[-1]]  # older event ranked first by meaning
    col.documents.search.return_value = {"hits": [{"document": event_to_doc(e)} for e in rows]}
    result = await search.ask("did she seem upset?", NOW, [])
    assert result.engine == "typesense" and result.mode == "related"
    assert result.answer == "Here are the closest moments I found."
    assert [h.event.id for h in result.hits] == [e.id for e in rows]
    params = col.documents.search.call_args.args[0]
    assert params["query_by"] == "text,embedding" and "sort_by" not in params
    assert params["exclude_fields"] == "embedding"


async def test_known_question_keeps_plan_and_python_sum():
    search, col = fake_search()
    col.documents.search.return_value = {"hits": [{"document": event_to_doc(e)} for e in events()]}
    result = await search.ask("how much has she drunk today?", NOW, [])
    assert result.mode == "exact" and f"About {TODAY_ML} ml" in result.answer
    assert col.documents.search.call_args.args[0]["query_by"] == "text,kind"


async def test_hybrid_keeps_date_filter_and_excludes_future():
    search, col = fake_search()
    from datetime import timedelta

    future = events()[0].model_copy(update={"ts": NOW + timedelta(hours=1)})
    col.documents.search.return_value = {"hits": [{"document": event_to_doc(future)}]}
    result = await search.ask("did she seem upset yesterday?", NOW, [])
    assert result.mode == "related" and not result.hits
    assert "ts_unix" in col.documents.search.call_args.args[0]["filter_by"]


async def test_ensure_adds_embedding_without_recreating():
    search, col = fake_search(fields=[{"name": "text", "type": "string"}])
    await search.ensure()
    assert col.update.call_args.args[0]["fields"][0]["embed"]["from"] == ["text"]
    col.delete.assert_not_called()
    assert search._hybrid_ready


async def test_failed_migration_defers_recreate_until_reindex():
    search, col = fake_search(fields=[{"name": "text", "type": "string"}])
    col.update.side_effect = RuntimeError("schema update unavailable")
    await search.ensure()
    assert not search._hybrid_ready and search._schema_outdated
    col.delete.assert_not_called()
    col.retrieve.side_effect = ObjectNotFound("deleted")
    assert await search.reindex(events()[:1]) == 1
    col.delete.assert_awaited_once()
    search.client.collections.create.assert_awaited_once()
    assert search._hybrid_ready and not search._schema_outdated


async def test_hybrid_error_uses_keyword_search():
    search, col = fake_search()
    col.documents.search.side_effect = [RuntimeError("model unavailable"), {"hits": []}]
    result = await search.ask("did she seem upset?", NOW, [])
    assert result.engine == "typesense" and result.mode == "exact"
    assert col.documents.search.call_count == 2
    assert col.documents.search.call_args.args[0]["query_by"] == "text,kind"


async def test_typesense_error_falls_back_without_related_claim():
    search, col = fake_search()
    col.retrieve.side_effect = RuntimeError("offline")
    result = await search.ask("did she seem upset?", NOW, events())
    assert result.engine == "fallback" and result.mode is None


def silent_wav() -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(32000))
    return base64.b64encode(buffer.getvalue()).decode()


async def test_voice_transcribes_over_post_then_answers_from_events():
    search, col = fake_search()
    q = "How much has she drunk today?"
    search.client.multi_search.perform.return_value = {
        "results": [{"request_params": {"voice_query": {"transcribed_query": q}}}],
    }
    col.documents.search.return_value = {"hits": [{"document": event_to_doc(e)} for e in events()]}
    audio = silent_wav()
    question = await search.transcribe(audio)
    result = await search.ask(question, NOW, [])
    assert result.q == q and result.mode == "exact" and f"About {TODAY_ML} ml" in result.answer
    posted = search.client.multi_search.perform.call_args.args[0]["searches"][0]
    assert posted["voice_query"] == audio and posted["collection"] == "test"
    assert "voice_query" not in col.documents.search.call_args.args[0]
    assert audio not in str(search.last_debug)


async def test_voice_upstream_error_never_logs_or_echoes_audio(caplog):
    search, _ = fake_search()
    audio = silent_wav()
    search.client.multi_search.perform.side_effect = RuntimeError(audio)
    with pytest.raises(ValueError, match="Please try typing") as error:
        await search.transcribe(audio)
    assert audio not in caplog.text and audio not in str(error.value)


async def test_silent_voice_has_plain_error():
    search, _ = fake_search()
    search.client.multi_search.perform.return_value = {
        "results": [{"request_params": {"voice_query": {"transcribed_query": ""}}}],
    }
    with pytest.raises(ValueError, match="couldn't hear"):
        await search.transcribe(silent_wav())


async def test_voice_schema_change_deferred_until_reindex():
    search, col = fake_search()
    col.retrieve.return_value.pop("voice_query_model")
    col.update.side_effect = RuntimeError("Only fields can be updated")
    await search.ensure()
    assert search._hybrid_ready and not search._voice_ready and search._schema_outdated
    col.delete.assert_not_called()
    with pytest.raises(ValueError, match="until search is reindexed"):
        await search.transcribe(silent_wav())
    col.retrieve.side_effect = ObjectNotFound("deleted")
    assert await search.reindex(events()[:1]) == 1
    assert search._voice_ready and not search._schema_outdated


@pytest.mark.parametrize("hybrid", [False, True])
@pytest.mark.parametrize("voice", [False, True])
async def test_feature_flags_independently_control_new_schema(hybrid, voice):
    search, _ = fake_search(missing=True, hybrid=hybrid, voice=voice)
    await search.ensure()
    body = search.client.collections.create.call_args.args[0]
    assert any(f["name"] == "embedding" for f in body["fields"]) is hybrid
    assert ("voice_query_model" in body) is voice
    assert search._hybrid_ready is hybrid
    assert search._voice_ready is voice


@pytest.mark.parametrize("existing_models", [False, True])
async def test_disabled_features_never_migrate_or_use_models(existing_models):
    fields = schema("test", "syn", hybrid=existing_models)["fields"]
    search, col = fake_search(fields=fields, hybrid=False, voice=False)
    if not existing_models:
        col.retrieve.return_value.pop("voice_query_model")
    result = await search.ask("did she seem upset?", NOW, [])
    assert result.mode == "exact"
    assert col.documents.search.call_args.args[0]["query_by"] == "text,kind"
    assert await search.reindex(events()[:1]) == 1
    assert not await search.voice_available()
    with pytest.raises(ValueError, match="Voice questions need search to be set up."):
        await search.transcribe(silent_wav())
    col.update.assert_not_called()
    col.delete.assert_not_called()
    search.client.multi_search.perform.assert_not_called()
    assert not search._schema_outdated


@pytest.mark.parametrize("hybrid", [False, True])
@pytest.mark.parametrize("voice", [False, True])
def test_env_flags_are_opt_in(monkeypatch, hybrid, voice):
    for name, on in [("TYPESENSE_HYBRID", hybrid), ("TYPESENSE_VOICE", voice)]:
        monkeypatch.delenv(name, raising=False)
        if on:
            monkeypatch.setenv(name, "1")
    search = TypesenseSearch()
    assert search.hybrid_enabled is hybrid
    assert search.voice_enabled is voice


def test_schema_defaults_do_not_load_models():
    body = schema("test", "syn")
    assert "embedding" not in [f["name"] for f in body["fields"]]
    assert "voice_query_model" not in body


async def test_voice_availability_requires_ready_model():
    search, col = fake_search(voice=True, hybrid=False)
    assert await search.voice_available()
    search._ready = False
    col.retrieve.return_value.pop("voice_query_model")
    col.update.side_effect = RuntimeError("unsupported")
    assert not await search.voice_available()


@pytest.mark.parametrize("hybrid", [False, True])
@pytest.mark.parametrize("voice", [False, True])
async def test_existing_schema_migrates_only_enabled_features(hybrid, voice):
    search, col = fake_search(fields=[{"name": "text", "type": "string"}], hybrid=hybrid, voice=voice)
    col.retrieve.return_value.pop("voice_query_model")
    await search.ensure()
    bodies = [call.args[0] for call in col.update.call_args_list]
    assert sum("fields" in body for body in bodies) == int(hybrid)
    assert sum("voice_query_model" in body for body in bodies) == int(voice)
    col.delete.assert_not_called()
