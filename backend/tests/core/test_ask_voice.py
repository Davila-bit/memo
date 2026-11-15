"""Ask HTTP validation and voice-to-planned-answer flow; no external calls."""

from hearth.integrations.search import FallbackSearch

from ..integrations.fixture import NOW, TODAY_ML, events
from ..integrations.test_search_features import fake_search, silent_wav


def test_voice_without_search_returns_plain_400(client):
    client.app.state.svc.search = FallbackSearch()
    response = client.post("/api/ask", json={"voice_wav_b64": silent_wav()})
    assert response.status_code == 400
    assert response.json()["detail"] == "Voice questions need search to be set up."


def test_oversize_voice_returns_400_before_search(client):
    search, _ = fake_search()
    client.app.state.svc.search = search
    response = client.post("/api/ask", json={"voice_wav_b64": "A" * 2_000_004})
    assert response.status_code == 400 and "under 1.5 MB" in response.json()["detail"]
    search.client.multi_search.perform.assert_not_called()


def test_invalid_voice_returns_plain_400(client):
    client.app.state.svc.search = fake_search()[0]
    response = client.post("/api/ask", json={"voice_wav_b64": "invalid audio"})
    assert response.status_code == 400 and "WAV recording" in response.json()["detail"]


def test_empty_text_returns_plain_400(client):
    response = client.post("/api/ask", json={"q": " "})
    assert response.status_code == 400 and "write a question" in response.json()["detail"]


def test_voice_returns_transcription_and_computed_answer(client, monkeypatch):
    from hearth.integrations.search import event_to_doc

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    search, col = fake_search()
    client.app.state.svc.search = search
    monkeypatch.setattr(client.app.state.svc.clock, "now", lambda: NOW)
    q = "How much has she drunk today?"
    search.client.multi_search.perform.return_value = {
        "results": [{"request_params": {"voice_query": {"transcribed_query": q}}}],
    }
    col.documents.search.return_value = {"hits": [{"document": event_to_doc(e)} for e in events()]}
    response = client.post("/api/ask", json={"voice_wav_b64": silent_wav()})
    assert response.status_code == 200, response.text
    assert response.json()["q"] == q and f"About {TODAY_ML} ml" in response.json()["answer"]


def test_disabled_voice_returns_existing_400_without_touching_search(client):
    search, col = fake_search(voice=False, hybrid=False)
    client.app.state.svc.search = search
    response = client.post("/api/ask", json={"voice_wav_b64": silent_wav()})
    assert response.status_code == 400
    assert response.json()["detail"] == "Voice questions need search to be set up."
    col.retrieve.assert_not_called()
    col.update.assert_not_called()
    search.client.multi_search.perform.assert_not_called()


def test_me_reports_voice_only_when_model_is_ready(client):
    search, col = fake_search(voice=False, hybrid=False)
    client.app.state.svc.search = search
    assert client.get("/api/me").json()["voice"] is False
    col.retrieve.assert_not_called()
    search.voice_enabled = True
    assert client.get("/api/me").json()["voice"] is True
    search._ready = False
    col.retrieve.return_value.pop("voice_query_model")
    col.update.side_effect = RuntimeError("unsupported")
    assert client.get("/api/me").json()["voice"] is False
    client.app.state.svc.search = FallbackSearch()
    assert client.get("/api/me").json()["voice"] is False
