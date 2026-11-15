"""No-key unit tests: fallbacks, answer maths, validation, payload shapes. No network."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from hearth_contracts.models import Alert, HearthEvent, PushSubscription

import hearth.integrations as integ
from hearth.integrations import digest as digest_mod
from hearth.integrations.askplan import make_plan
from hearth.integrations.notify import HearthNotifier, ntfy_body, push_payload
from hearth.integrations.redact import RegexRedactor, regex_redact
from hearth.integrations.search import (
    FallbackSearch,
    _fields_ok,
    _sort_ok,
    doc_to_event,
    event_to_doc,
    schema,
)

from .fixture import NOW, TODAY_DRINKS, TODAY_ML, events
from .test_search_live import QUESTIONS

KEYS = ["TYPESENSE_HOST", "TYPESENSE_API_KEY", "GEMINI_API_KEY", "VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY",
        "NTFY_TOPIC", "HEARTH_REDACT_NAMES"]


@pytest.fixture(autouse=True)
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------- factories / health


def test_factories_fall_back_without_keys() -> None:
    assert isinstance(integ.build_search(), FallbackSearch)
    h = integ.health()
    assert set(h) == {"typesense", "gemini_text", "web_push", "ntfy", "presidio"}
    assert h["typesense"] == h["gemini_text"] == h["web_push"] == h["ntfy"] == "missing_key"
    assert integ.build_digest_writer() is not None
    assert integ.build_notifier(lambda: _subs([])) is not None


async def _subs(s: list[PushSubscription]) -> list[PushSubscription]:
    return s


# ---------------------------------------------------------------------------- search fallback


@pytest.mark.parametrize("q,expect", QUESTIONS, ids=[q for q, _ in QUESTIONS])
async def test_fallback_answers_same_questions(q: str, expect: list[str]) -> None:
    r = await FallbackSearch().ask(q, NOW, events())
    assert r.engine == "fallback"
    for piece in expect:
        assert piece in r.answer, r.answer


async def test_fallback_unknown_question_says_so() -> None:
    r = await FallbackSearch().ask("did she go swimming?", NOW, events())
    assert "couldn't find" in r.answer
    r = await FallbackSearch().ask("how much has she drunk today?", NOW, [])
    assert "couldn't find" in r.answer


def test_amount_is_a_python_sum() -> None:
    local = digest_mod.to_local
    today = [e for e in events() if e.kind == "drink" and local(e.ts).date() == local(NOW).date()]
    assert sum(e.volume_ml_est or 0 for e in today) == TODAY_ML and len(today) == TODAY_DRINKS


def test_plan_resolves_relative_days() -> None:
    p = make_plan("did Mum take her evening pills on Tuesday?", NOW)
    assert p.intent == "yesno" and p.topic == "med" and p.dayparts == ["evening"]
    assert p.start is not None and p.start.weekday() == 1 and p.timeframe == "on Tuesday"
    p = make_plan("any check-ins she missed this week?", NOW)
    assert p.kinds == ["missed"] and p.start is not None and (NOW - p.start) < timedelta(days=7)


def test_doc_and_schema() -> None:
    d = event_to_doc(events()[0])
    assert d["day"] == "2026-11-09" and d["weekday"] == "Monday" and d["daypart"] == "morning"
    assert d["time_label"] == "8:00 am" and "blue mug" in d["text"]
    s = schema("x", "x-syn")
    facets = {f["name"] for f in s["fields"] if f.get("facet")}
    assert {"kind", "day", "daypart", "weekday", "source"} <= facets
    assert s["default_sorting_field"] == "ts_unix" and s["synonym_sets"] == ["x-syn"]
    refused = events()[0].model_copy(update={"kind": "refused", "for_kind": "med"})
    assert doc_to_event(event_to_doc(refused)) == refused
    assert {"name": "for_kind", "type": "string", "index": False, "optional": True} in s["fields"]


def test_filter_allow_list() -> None:
    assert _fields_ok("kind:=drink && ts_unix:[1..2] && daypart:=evening")
    assert not _fields_ok("kind:=refused && text:drink")
    assert not _fields_ok("secret_field:=1")
    assert _sort_ok("ts_unix:desc") and not _sort_ok("summary:asc") and not _sort_ok("ts_unix:sideways")


# ---------------------------------------------------------------------------- redaction


def test_regex_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEARTH_REDACT_NAMES", "Margaret,Sarah")
    r = RegexRedactor()
    assert r.redact("Held up the blue mug, drank twice") == "Held up the blue mug, drank twice"
    out = r.redact("Sarah's number is (404) 555-0187, email sarah.e@example.com, at 12 Oak Tree Lane")
    assert "[name]" in out and "[phone]" in out and "[email]" in out and "[address]" in out
    assert "555" not in out and "Oak" not in out
    assert "Margaret" not in regex_redact("margaret said no")
    assert regex_redact("SSN 123-45-6789") == "SSN [id number]"
    assert regex_redact("") == ""


def test_presidio_redactor_regex_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEARTH_REDACT_NAMES", "Margaret")
    r = integ.PresidioRedactor(eager=False)  # never loads: must still redact with regex
    assert not r.ready
    assert r.redact("Margaret rang 404-555-0187") == "[name] rang [phone]"


# ---------------------------------------------------------------------------- digest


def _day_events() -> list[HearthEvent]:
    return [e for e in events() if e.ts.astimezone(digest_mod.to_local(e.ts).tzinfo).day == 11]


async def test_digest_template_without_key() -> None:
    d = await integ.build_digest_writer().write("2026-11-11", _day_events(), "Mum")
    assert d.generated_by == "template"
    assert "!" not in d.text and len(d.text.split()) <= 60
    assert d.text.startswith("Mum had") and "about" in d.text


async def test_digest_validation_rejects_bad_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    outputs = iter([
        "Mum drank 900 ml today. She refused once. Try tea.",  # 900 isn't in the input
        "Mum did well today! Great job. Keep going.",  # exclamation
        "Mum may be dehydrated. Call her doctor. Check her dose.",  # medical
    ])

    async def fake_generate(prompt: str, **kw) -> str:
        return next(outputs)

    monkeypatch.setattr(digest_mod.gemini, "generate", fake_generate)
    w = digest_mod.GeminiDigestWriter()
    for i in range(3):
        d = await w.write("2026-11-11", _day_events()[: 5 + i], "Mum")
        assert d.generated_by == "template"


async def test_digest_accepts_good_output_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    calls = 0
    evs = _day_events()
    total = digest_mod.totals(evs).fluid_ml

    async def fake_generate(prompt: str, **kw) -> str:
        nonlocal calls
        calls += 1
        assert "she_said" in prompt and str(total) in prompt
        return (f"Mum drank about {total} ml and ate her lunch. She said no to water at 2:10 pm. "
                "A cup of tea in the afternoon might suit her.")

    monkeypatch.setattr(digest_mod.gemini, "generate", fake_generate)
    w = digest_mod.GeminiDigestWriter()
    a = await w.write("2026-11-11", evs, "Mum")
    b = await w.write("2026-11-11", evs, "Mum")
    assert a.generated_by == "gemini-3.5-flash-lite" and a == b and calls == 1


# ---------------------------------------------------------------------------- notifier


def _alert(kind: str = "concern", tier: str = "urgent") -> Alert:
    return Alert(id="al-1", ts=NOW, resident_id="margaret", kind=kind, tier=tier,
                 title="Mum may need you", detail="Asked once, kindly.", said="I fell down")


def test_payload_shapes() -> None:
    p = push_payload("t", "b", urgent=True, url="/alerts/1", actions=[("handling", "I'm handling it")])
    assert set(p) == {"title", "body", "url", "urgent", "actions", "tag", "ts"}
    assert p["actions"] == [{"action": "handling", "title": "I'm handling it"}]
    n = ntfy_body("topic", "Mum – urgent ✓", "body", urgent=True, url="/alerts/1",
                  actions=[("handling", "I'm handling it"), ("called_911", "Call 911")])
    assert n["priority"] == 5 and n["actions"][1]["url"] == "tel:911"
    assert n["click"].endswith("/alerts/1") and n["actions"][0]["url"].endswith("?action=handling")


async def test_notifier_without_keys_is_silent() -> None:
    n = HearthNotifier(lambda: _subs([]))
    await n.alert(_alert())  # no keys: nothing to do, must not raise


async def test_notifier_sends_and_drops_gone_subscriptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "x" * 43)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "y" * 87)
    monkeypatch.setenv("NTFY_TOPIC", "hearth-test-topic")
    posted: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        posted.append(json.loads(req.content))
        return httpx.Response(200, json={"id": "x"})

    sent: list[tuple[str, dict]] = []

    class Gone(Exception):
        response = httpx.Response(410)

    def fake_webpush(subscription_info: dict, data: str, **kw) -> None:
        if "gone" in subscription_info["endpoint"]:
            raise Gone()
        sent.append((subscription_info["endpoint"], json.loads(data)))

    subs = [PushSubscription(endpoint="https://push.example/ok", keys={"p256dh": "a", "auth": "b"}),
            PushSubscription(endpoint="https://push.example/gone", keys={"p256dh": "a", "auth": "b"})]
    n = HearthNotifier(lambda: _subs(subs), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                       webpush_fn=fake_webpush)
    await n.alert(_alert())
    assert len(sent) == 1 and sent[0][1]["urgent"] is True
    assert "she said 'I fell down'" in sent[0][1]["body"]
    assert [a["action"] for a in sent[0][1]["actions"]] == ["handling", "called_911"]
    assert posted and posted[0]["priority"] == 5 and posted[0]["topic"] == "hearth-test-topic"
    assert n.pop_dead_endpoints() == ["https://push.example/gone"] and n.pop_dead_endpoints() == []

    posted.clear()
    await n.alert(_alert("refused", "check"))
    assert posted == []  # non-urgent: no ntfy unless NTFY_ALL=1


async def test_notifier_swallows_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NTFY_TOPIC", "t")

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    n = HearthNotifier(lambda: _subs([]), http=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    await n.notify("t", "b", urgent=True)
    assert integ.health()["ntfy"] == "error"


def test_real_vapid_signing_offline(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The VAPID key format scripts/vapid-keys makes is accepted by pywebpush (curl mode: nothing is sent)."""
    import os

    monkeypatch.chdir(tmp_path)  # curl mode writes ./encrypted.data

    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import Vapid
    from py_vapid.utils import b64urlencode
    from pywebpush import webpush

    from hearth.integrations.notify import DEFAULT_SUBJECT

    v = Vapid()
    v.generate_keys()
    priv = b64urlencode(v.private_key.private_numbers().private_value.to_bytes(32, "big"))
    browser = ec.generate_private_key(ec.SECP256R1())
    keys = {"p256dh": b64urlencode(browser.public_key().public_bytes(ser.Encoding.X962,
                                                                     ser.PublicFormat.UncompressedPoint)),
            "auth": b64urlencode(os.urandom(16))}
    out = webpush({"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": keys},
                  data=json.dumps(push_payload("t", "b", urgent=False, url="/", actions=None)),
                  vapid_private_key=priv, vapid_claims={"sub": DEFAULT_SUBJECT}, curl=True)
    assert "vapid t=" in out
