"""Search = "Ask the log", on Typesense Cloud.

Typesense features in use:
- a typed collection with facets (`kind`, `day`, `daypart`, `weekday`, `source`, ...) and default sort `ts_unix`
- a v30 synonym set (water/drink/tea..., pills/tablets..., lunch/dinner..., refused/declined...)
- built-in Natural Language Search with a Google Gemini model (`nl_search_models`): Typesense asks
  Gemini to turn the question into `q` + `filter_by` + `sort_by`; the demo-clock date table is in the
  model's system prompt so "Tuesday" and "this morning" resolve
- upserts / bulk import, highlights for snippets
- hybrid keyword + automatic MiniLM embeddings for related moments
- Whisper voice questions via POST multi_search (audio is never stored or logged)
If NL search fails or returns an unusable filter, one direct Gemini call does the same translation
(validated against an allow-list), then a deterministic parser. Answers are computed in Python.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import re
import wave
from datetime import UTC, datetime, timedelta
from typing import Any

from hearth_contracts.models import AskResponse, HearthEvent

from . import gemini
from ._common import (
    day_str,
    daypart,
    env,
    gemini_key,
    gemini_text_model,
    local_tz,
    log,
    set_status,
    time_label,
    to_local,
    weekday,
)
from .askplan import SYNONYMS, Plan, compose_answer, keyword_hits, make_plan, matches, to_hits

FILTER_FIELDS = {"kind", "day", "daypart", "weekday", "ts_unix", "source", "amount", "concern_category",
                 "volume_ml_est", "confidence", "resident_id", "checkin_id", "hour"}
SORT_FIELDS = {"ts_unix", "volume_ml_est", "confidence", "_text_match"}
QUERY_BY = "text,kind"
VOICE_MODEL = {"model_name": "ts/whisper/base.en"}
MAX_VOICE_BYTES = 1_500_000


def validate_voice(audio: str) -> None:
    """Bound memory before decoding, then accept only the browser's PCM WAV format."""
    if len(audio) > 4 * ((MAX_VOICE_BYTES + 2) // 3):
        raise ValueError("Please keep your voice question under 1.5 MB.")
    try:
        raw = base64.b64decode(audio, validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError("Please send your voice question as a WAV recording.") from e
    if len(raw) > MAX_VOICE_BYTES:
        raise ValueError("Please keep your voice question under 1.5 MB.")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth(), wav.getcomptype()) != (1, 16000, 2, "NONE"):
                raise ValueError("Please record a mono, 16 kHz PCM WAV question.")
    except (wave.Error, EOFError) as e:
        raise ValueError("Please send your voice question as a WAV recording.") from e


def embedding_field() -> dict[str, Any]:
    return {"name": "embedding", "type": "float[]", "optional": True,
            "embed": {"from": ["text"], "model_config": {"model_name": "ts/all-MiniLM-L12-v2"}}}


def event_to_doc(e: HearthEvent) -> dict[str, Any]:
    ts = e.ts if e.ts.tzinfo else e.ts.replace(tzinfo=UTC)
    doc: dict[str, Any] = {
        "id": e.id,
        "ts": ts.isoformat(),
        "ts_unix": int(ts.timestamp()),
        "resident_id": e.resident_id,
        "kind": e.kind,
        "source": e.source,
        "confidence": float(e.confidence),
        "summary": e.summary,
        "evidence": e.evidence or "",
        "day": day_str(ts),
        "daypart": daypart(ts),
        "weekday": weekday(ts),
        "hour": to_local(ts).hour,
        "time_label": time_label(ts),
        "text": " · ".join(x for x in (e.summary, e.evidence, e.said or "") if x),
    }
    for k in ("checkin_id", "for_kind", "amount", "volume_ml_est", "said", "concern_category"):
        v = getattr(e, k)
        if v is not None:
            doc[k] = v
    if e.med_steps:
        doc["med_steps"] = list(e.med_steps)
    return doc


def doc_to_event(d: dict[str, Any]) -> HearthEvent:
    keys = set(HearthEvent.model_fields)
    return HearthEvent.model_validate({k: v for k, v in d.items() if k in keys})


def schema(name: str, synonym_set: str, *, hybrid: bool = False, voice: bool = False) -> dict[str, Any]:
    f = lambda n, t, **kw: {"name": n, "type": t, **kw}
    return {
        "name": name,
        "fields": [
            f("ts", "string", index=False, optional=True),
            f("ts_unix", "int64", sort=True),
            f("resident_id", "string", facet=True),
            f("checkin_id", "string", optional=True),
            f("kind", "string", facet=True),
            f("for_kind", "string", index=False, optional=True),
            f("source", "string", facet=True),
            f("confidence", "float"),
            f("summary", "string"),
            f("evidence", "string", optional=True),
            f("said", "string", optional=True),
            f("amount", "string", facet=True, optional=True),
            f("volume_ml_est", "int32", facet=True, optional=True),
            f("med_steps", "string[]", facet=True, optional=True),
            f("concern_category", "string", facet=True, optional=True),
            f("day", "string", facet=True),
            f("daypart", "string", facet=True),
            f("weekday", "string", facet=True),
            f("hour", "int32", facet=True),
            f("time_label", "string", index=False, optional=True),
            f("text", "string"),
            *([embedding_field()] if hybrid else []),
        ],
        "default_sorting_field": "ts_unix",
        "synonym_sets": [synonym_set],
        **({"voice_query_model": VOICE_MODEL.copy()} if voice else {}),
    }


def date_table(now: datetime) -> str:
    """Plain-text calendar the model needs to resolve relative dates against the demo clock."""
    n = to_local(now)
    lines = [
        (f"Current local date and time: {n.strftime('%A %Y-%m-%d %H:%M')} ({local_tz().key}); "
         f"now ts_unix = {int(now.timestamp())}."),
        "Days (local), newest first, with the ts_unix range of each day:",
    ]
    for back in range(8):
        d = (n - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
        tag = " (today)" if back == 0 else " (yesterday)" if back == 1 else ""
        lines.append(f"- {d.strftime('%A')} {d.strftime('%Y-%m-%d')}{tag}: ts_unix {int(d.timestamp())} to "
                     f"{int((d + timedelta(days=1)).timestamp()) - 1}")
    monday = (n - timedelta(days=n.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    lines.append(f"'This week' starts Monday {monday.strftime('%Y-%m-%d')}: ts_unix >= {int(monday.timestamp())}.")
    return "\n".join(lines)


GUIDE = """You translate a family member's question about her mother's care log into Typesense search parameters.
Each document is one event. Field `kind` is one of: drink, meal, med, refused, missed, concern, note.
- drink = observed drinking (water, tea, juice); meal = observed eating; med = observed taking tablets/pills.
- refused = she said no to a drink, meal or tablets (the summary says which); `said` holds what she said.
- missed = a check-in call she did not answer.
Fields `day` (YYYY-MM-DD), `weekday` (e.g. Tuesday), `daypart` (morning 05-12, afternoon 12-17 incl. lunch,
evening 17-21 incl. dinner and evening tablets, night) are local time. `ts_unix` is seconds.
Rules:
- Dates: always filter on `ts_unix` with the ranges in the table below, never on `day`. A single day:
  `ts_unix:[START..END]` (resolve weekday names to the most recent such day up to today). 'This week':
  `ts_unix:>=N`.
- Questions about refusing / saying no: `kind:=refused`. Missed check-ins: `kind:=missed`.
- 'how much has she drunk': `kind:=drink`. Pills / tablets questions: `kind:[med,refused]`.
- 'evening', 'morning' etc: add `daypart:=...`. Lunch: `daypart:=afternoon`. Dinner: `daypart:=evening`.
- 'last' / 'most recent' / 'when': sort_by `ts_unix:desc`.
- Keep q as `*` unless one specific word (e.g. lunch, tea, drink) must appear; put such words in q, never
  in filter_by (there is no `text` filter).
- Only use these filter fields: kind, daypart, ts_unix, source, amount, volume_ml_est.
"""


def _fields_ok(filter_by: str) -> bool:
    names = re.findall(r"([A-Za-z_][A-Za-z0-9_.]*)\s*:", filter_by or "")
    return all(n in FILTER_FIELDS for n in names)


def _sort_ok(sort_by: str) -> bool:
    if not sort_by:
        return True
    for part in sort_by.split(","):
        m = re.fullmatch(r"\s*([A-Za-z_]+)\s*:\s*(asc|desc)\s*", part)
        if not m or m.group(1) not in SORT_FIELDS:
            return False
    return True


def plan_filter(plan: Plan) -> str:
    parts = []
    if plan.kinds:
        parts.append(f"kind:[{','.join(plan.kinds)}]")
    if plan.start is not None:
        parts.append(f"ts_unix:>={int(plan.start.timestamp())}")
    if plan.end is not None:
        parts.append(f"ts_unix:<{int(plan.end.timestamp())}")
    if plan.dayparts:
        parts.append(f"daypart:[{','.join(plan.dayparts)}]")
    return " && ".join(parts)


class TypesenseSearch:
    """Implements ports.Search."""

    def __init__(self, *, collection: str | None = None, nl_model_id: str | None = None,
                 synonym_set: str | None = None, use_nl: bool = True,
                 hybrid: bool | None = None, voice: bool | None = None) -> None:
        self.collection = collection or env("TYPESENSE_COLLECTION", "hearth_events") or "hearth_events"
        self.nl_model_id = nl_model_id or f"{self.collection}-gemini"
        self.synonym_set = synonym_set or f"{self.collection}-synonyms"
        self.use_nl = use_nl and bool(gemini_key())
        self.hybrid_enabled = env("TYPESENSE_HYBRID") == "1" if hybrid is None else hybrid
        self.voice_enabled = env("TYPESENSE_VOICE") == "1" if voice is None else voice
        self._client = None
        self._ready = False
        self._hybrid_ready = False
        self._voice_ready = False
        self._schema_outdated = False
        self._nl_ready = False
        self._nl_prompt_key: str | None = None
        self._lock = asyncio.Lock()
        self.last_debug: dict[str, Any] = {}

    # ------------------------------------------------------------------ setup

    def _make_client(self):
        import typesense

        host = env("TYPESENSE_HOST") or ""
        if "://" in host:
            from urllib.parse import urlparse

            u = urlparse(host)
            node = {"host": u.hostname, "port": u.port or (443 if u.scheme == "https" else 8108),
                    "protocol": u.scheme}
        else:
            h, _, p = host.partition(":")
            node = {"host": h, "port": int(p) if p else 443, "protocol": "https" if not p or p == "443" else "http"}
        return typesense.AsyncClient({
            "api_key": env("TYPESENSE_API_KEY") or "",
            "nodes": [node],
            "connection_timeout_seconds": 10,
            "num_retries": 1,
        })

    @property
    def client(self):
        if self._client is None:
            self._client = self._make_client()
        return self._client

    async def ensure(self) -> None:
        if self._ready:
            return
        from typesense.exceptions import ObjectNotFound

        async with self._lock:
            if self._ready:
                return
            await self.client.synonym_sets[self.synonym_set].upsert({
                "items": [{"id": k, "synonyms": v} for k, v in SYNONYMS.items()],
            })
            try:
                current = await self.client.collections[self.collection].retrieve()
                self._hybrid_ready = self.hybrid_enabled and any(
                    f["name"] == "embedding" for f in current.get("fields", []))
                if self.hybrid_enabled and not self._hybrid_ready:
                    try:
                        await self.client.collections[self.collection].update({"fields": [embedding_field()]})
                        self._hybrid_ready = True
                    except Exception as e:  # noqa: BLE001
                        self._schema_outdated = True
                        log.warning("typesense: embedding migration deferred until reindex: %s", _err(e))
                self._voice_ready = self.voice_enabled and current.get("voice_query_model") == VOICE_MODEL
                if self.voice_enabled and not self._voice_ready:
                    try:
                        await self.client.collections[self.collection].update({"voice_query_model": VOICE_MODEL.copy()})
                        updated = await self.client.collections[self.collection].retrieve()
                        self._voice_ready = updated.get("voice_query_model") == VOICE_MODEL
                    except Exception:  # noqa: BLE001
                        self._voice_ready = False
                    if not self._voice_ready:
                        self._schema_outdated = True
                        log.warning("typesense: voice schema migration deferred until reindex")
            except ObjectNotFound:
                await self.client.collections.create(schema(
                    self.collection, self.synonym_set, hybrid=self.hybrid_enabled, voice=self.voice_enabled))
                self._hybrid_ready = self.hybrid_enabled
                self._voice_ready = self.voice_enabled
                self._schema_outdated = False
                log.info("typesense: created collection %s", self.collection)
            self._ready = True
            set_status("typesense", "ok")

    async def _ensure_nl(self, now: datetime) -> bool:
        """Create the NL search model once; refresh its system prompt when the demo-clock minute changes."""
        if not self.use_nl:
            return False
        from typesense.exceptions import ObjectNotFound

        prompt = GUIDE + "\n" + date_table(now)
        key = to_local(now).strftime("%Y-%m-%d %H:%M")
        try:
            if not self._nl_ready:
                body = {"model_name": f"google/{gemini_text_model()}", "api_key": gemini_key(),
                        "max_bytes": 16000, "temperature": 0.0, "system_prompt": prompt}
                try:
                    await self.client.nl_search_models[self.nl_model_id].update(body)
                except ObjectNotFound:
                    await self.client.nl_search_models.create({"id": self.nl_model_id, **body})
                self._nl_ready, self._nl_prompt_key = True, key
            elif key != self._nl_prompt_key:
                await self.client.nl_search_models[self.nl_model_id].update({"system_prompt": prompt})
                self._nl_prompt_key = key
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("typesense NL model unavailable, using direct translation: %s", _err(e))
            self.use_nl = False
            return False

    # ------------------------------------------------------------------ Search port

    async def index(self, event: HearthEvent) -> None:
        try:
            await self.ensure()
            await self.client.collections[self.collection].documents.upsert(event_to_doc(event))
        except Exception as e:  # noqa: BLE001
            set_status("typesense", "error")
            log.warning("typesense index failed: %s", _err(e))

    async def reindex(self, events: list[HearthEvent]) -> int:
        """Bulk upsert (seeding / demo reset). Returns how many documents were accepted."""
        try:
            await self.ensure()
            if self._schema_outdated:
                # The caller supplies the authoritative events; ensure() never deletes a collection.
                await self.client.collections[self.collection].delete()
                self._ready = False
                await self.ensure()
                self._schema_outdated = False
            if not events:
                return 0
            res = await self.client.collections[self.collection].documents.import_(
                [event_to_doc(e) for e in events], {"action": "upsert"})
            return sum(1 for r in res if r.get("success"))
        except Exception as e:  # noqa: BLE001
            set_status("typesense", "error")
            log.warning("typesense reindex failed: %s", _err(e))
            return 0

    async def clear(self) -> None:
        """Delete every document (privacy: 'delete everything stored about her')."""
        try:
            await self.ensure()
            await self.client.collections[self.collection].documents.delete({"filter_by": "ts_unix:>0"})
        except Exception as e:  # noqa: BLE001
            log.warning("typesense clear failed: %s", _err(e))

    async def drop(self) -> None:
        """Remove the collection, synonym set and NL model (tests)."""
        for coro in (self.client.collections[self.collection].delete(),
                     self.client.synonym_sets[self.synonym_set].delete(),
                     self.client.nl_search_models[self.nl_model_id].delete()):
            try:
                await coro
            except Exception:  # noqa: BLE001, S110
                pass

    async def ask(self, q: str, now: datetime, recent: list[HearthEvent]) -> AskResponse:
        plan = make_plan(q, now)
        try:
            await self.ensure()
            events, snippets, how = await self._search(q, plan, now)
            self.last_debug["how"] = how
            related = how == "hybrid"
            answer = "Here are the closest moments I found." if related else compose_answer(q, plan, events, now)
            return AskResponse(q=q, answer=answer, hits=to_hits(events, snippets),
                               engine="typesense", mode="related" if related else "exact")
        except Exception as e:  # noqa: BLE001
            set_status("typesense", "error")
            log.warning("typesense ask failed, using keyword fallback: %s", _err(e))
            return fallback_ask(q, now, recent)

    # ------------------------------------------------------------------ internals

    async def voice_available(self) -> bool:
        if not self.voice_enabled:
            return False
        try:
            await self.ensure()
        except Exception:  # noqa: BLE001
            return False
        return self._voice_ready

    async def transcribe(self, audio: str) -> str:
        if not self.voice_enabled:
            raise ValueError("Voice questions need search to be set up.")
        validate_voice(audio)
        try:
            await self.ensure()
        except Exception:  # noqa: BLE001
            raise ValueError("I couldn't write down your question. Please try typing it.") from None
        if not self._voice_ready:
            raise ValueError("Voice questions are unavailable until search is reindexed. Please type your question.")
        try:
            # POST keeps audio out of URLs/access logs and avoids Typesense's GET length limit.
            response = await self.client.multi_search.perform({"searches": [{
                "collection": self.collection, "q": "*", "query_by": "text", "per_page": 1,
                "exclude_fields": "embedding", "voice_query": audio,
            }]})
            result = response["results"][0]
            q = str((result.get("request_params", {}).get("voice_query") or {}).get("transcribed_query") or "").strip()
        except Exception:  # noqa: BLE001
            # Do not log the exception: an upstream error could echo its request body.
            raise ValueError("I couldn't write down your question. Please try typing it.") from None
        if not 2 <= len(q) <= 300:
            raise ValueError("I couldn't hear a short question. Please try again or type it.")
        return q

    async def _run(self, params: dict[str, Any]) -> dict[str, Any]:
        base = {"query_by": QUERY_BY, "per_page": 100, "highlight_fields": "text",
                "facet_by": "kind,daypart", "max_facet_values": 10, "exclude_fields": "embedding"}
        merged = {k: v for k, v in {**base, **params}.items() if v not in ("", None)}
        merged.setdefault("q", "*")
        return await self.client.collections[self.collection].documents.search(merged)

    async def _search(self, q: str, plan: Plan, now: datetime) -> tuple[list[HearthEvent], dict[str, str], str]:
        tried: list[str] = []
        # Unrecognised questions find related evidence, never a yes/no or count from semantic matches.
        if not plan.kinds and self._hybrid_ready:
            try:
                filters = [plan_filter(plan), f"ts_unix:<={int((now + timedelta(minutes=1)).timestamp())}"]
                res = await self._run({"q": q, "query_by": "text,embedding",
                                       "filter_by": " && ".join(f for f in filters if f),
                                       "per_page": 10})
                events, snips = self._hits(res, plan, now, sort_recent=False)
                return events, snips, "hybrid"
            except Exception as e:  # noqa: BLE001
                log.warning("typesense hybrid search unavailable, using keyword search: %s", _err(e))
                res = await self._run({"q": plan.text, "filter_by": plan_filter(plan), "sort_by": "ts_unix:desc"})
                events, snips = self._hits(res, plan, now, strict=True)
                return events, snips, "keyword"
        # 1. Typesense built-in natural-language search (Gemini model configured in Typesense).
        if await self._ensure_nl(now):
            try:
                res = await self._run({"q": q, "nl_query": True, "nl_model_id": self.nl_model_id})
                parsed = res.get("parsed_nl_query") or {}
                gen = parsed.get("generated_params") or {}
                self.last_debug["nl"] = {"generated": gen, "parse_time_ms": parsed.get("parse_time_ms"),
                                         "error": res.get("error") or parsed.get("error")}
                if (res.get("error") or parsed.get("error") or not gen
                        or not _fields_ok(gen.get("filter_by", "")) or not _sort_ok(gen.get("sort_by", ""))):
                    tried.append("nl:unusable")
                else:
                    events, snips = self._hits(res, plan, now)
                    if events:
                        return events, snips, "typesense_nl"
                    # Typesense 30.2 returns no hits from an NL query whose generated filter has `&&`
                    # (single-clause filters work; the same params as a plain search work). Re-run the
                    # parameters Typesense generated as a regular search.
                    aug = parsed.get("augmented_params") or gen
                    if "&&" in (aug.get("filter_by") or ""):
                        res = await self._run({"q": aug.get("q") or "*", "filter_by": aug.get("filter_by"),
                                               "sort_by": aug.get("sort_by") or "ts_unix:desc"})
                        events, snips = self._hits(res, plan, now)
                        if events:
                            return events, snips, "typesense_nl_rerun"
                    tried.append("nl:empty")
            except Exception as e:  # noqa: BLE001
                tried.append(f"nl:{type(e).__name__}")
                log.warning("typesense NL search failed: %s", _err(e))

        # 2. One direct Gemini call -> {q, filter_by, sort_by}, validated, run on Typesense.
        params = await translate_with_gemini(q, now)
        if params:
            self.last_debug["gemini"] = params
            res = await self._run(params)
            events, snips = self._hits(res, plan, now)
            if events:
                return events, snips, "gemini_translate"
            tried.append("gemini:empty")

        # 3. Deterministic plan -> Typesense filter.
        params = {"q": plan.text, "filter_by": plan_filter(plan), "sort_by": "ts_unix:desc"}
        self.last_debug["plan"] = params
        res = await self._run(params)
        events, snips = self._hits(res, plan, now, strict=True)
        return events, snips, "plan" + (f" after {','.join(tried)}" if tried else "")

    def _hits(self, res: dict[str, Any], plan: Plan, now: datetime,
              strict: bool = False, sort_recent: bool = True) -> tuple[list[HearthEvent], dict[str, str]]:
        events: list[HearthEvent] = []
        snips: dict[str, str] = {}
        for h in res.get("hits") or []:
            try:
                e = doc_to_event(h["document"])
            except Exception:  # noqa: BLE001, S112
                continue
            if e.ts > now + timedelta(minutes=1):
                continue  # after the demo clock: not "so far"
            if strict and not matches(e, plan):
                continue
            events.append(e)
            for hl in h.get("highlights") or []:
                if hl.get("field") == "text" and hl.get("snippet"):
                    snips[e.id] = re.sub(r"</?mark>", "", hl["snippet"])[:240]
        if sort_recent:
            events.sort(key=lambda e: e.ts, reverse=True)
        return events, snips


async def translate_with_gemini(q: str, now: datetime) -> dict[str, str] | None:
    if not gemini_key():
        return None
    prompt = (f"Question: {q}\n\nReply with JSON only: "
              '{"q": "...", "filter_by": "...", "sort_by": "..."} (use "" when not needed).')
    raw = await gemini.generate(prompt, system=GUIDE + "\n" + date_table(now), json_mode=True, timeout_s=8,
                                temperature=0.0)
    if not raw:
        return None
    try:
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip())
        out = {"q": str(data.get("q") or "*")[:80], "filter_by": str(data.get("filter_by") or ""),
               "sort_by": str(data.get("sort_by") or "ts_unix:desc")}
    except Exception:  # noqa: BLE001
        return None
    if not _fields_ok(out["filter_by"]) or not _sort_ok(out["sort_by"]):
        log.warning("gemini translation used fields outside the allow-list; ignored")
        return None
    return out


def fallback_ask(q: str, now: datetime, recent: list[HearthEvent]) -> AskResponse:
    try:
        plan, found = keyword_hits(q, now, recent)
        return AskResponse(q=q, answer=compose_answer(q, plan, found, now), hits=to_hits(found), engine="fallback")
    except Exception as e:  # noqa: BLE001
        log.warning("fallback ask failed: %s", type(e).__name__)
        return AskResponse(q=q, answer="I couldn't find anything about that in the log.", hits=[], engine="fallback")


class FallbackSearch:
    """Keyword + date heuristics over the `recent` events the core passes in."""

    async def index(self, event: HearthEvent) -> None:
        return None

    async def reindex(self, events: list[HearthEvent]) -> int:
        return 0

    async def clear(self) -> None:
        return None

    async def ask(self, q: str, now: datetime, recent: list[HearthEvent]) -> AskResponse:
        return fallback_ask(q, now, recent)


def _err(e: Exception) -> str:
    """Error text without secrets (Typesense errors carry only status + message)."""
    return f"{type(e).__name__}: {str(e)[:160]}"
