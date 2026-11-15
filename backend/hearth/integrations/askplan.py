"""Question understanding and answer writing for "Ask the log", shared by Typesense and the fallback.

The answer is always computed here, in Python, from the events that were found: counts, sums and
times are never produced by a model, so nothing is invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from hearth_contracts.models import AskHit, HearthEvent

from ._common import day_phrase, day_str, daypart, local_tz, time_label, to_local, weekday

WORDS = {
    "drink": ["water", "drink", "drinks", "drank", "drunk", "tea", "juice", "fluids", "fluid", "glass", "cup",
              "mug", "sip", "thirsty"],
    "med": ["pills", "pill", "tablets", "tablet", "meds", "med", "medication", "medicine", "medicines"],
    "meal": ["lunch", "dinner", "breakfast", "supper", "meal", "meals", "food", "ate", "eat", "eaten", "eating"],
    "refused": ["refused", "refuse", "refusal", "declined", "decline", "said no", "didn't want", "did not want",
                "wouldn't", "turned down"],
    "missed": ["missed", "miss", "didn't answer", "did not answer", "no answer", "unanswered"],
    "concern": ["fell", "fall", "fallen", "hurt", "help", "concern", "worried", "unwell", "pain"],
}

# Typesense synonym set (multi-way), also used by the fallback keyword match.
SYNONYMS: dict[str, list[str]] = {
    "fluids": ["water", "drink", "drinks", "tea", "juice", "fluids", "glass", "cup"],
    "meds": ["pills", "tablets", "meds", "med", "medication", "medicine"],
    "food": ["lunch", "dinner", "breakfast", "meal", "food", "ate"],
    "refusal": ["refused", "declined", "said no", "didn't want"],
}

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MEAL_DAYPART = {"breakfast": "morning", "lunch": "afternoon", "dinner": "evening", "supper": "evening"}

Intent = str  # "amount" | "last" | "said" | "missed" | "yesno" | "list"


def _has(q: str, words: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", q) for w in words)


@dataclass
class Plan:
    intent: Intent
    topic: str | None  # drink | meal | med
    kinds: list[str]  # event kinds to filter on (empty = any)
    start: datetime | None = None  # inclusive, UTC-aware
    end: datetime | None = None  # exclusive
    dayparts: list[str] = field(default_factory=list)
    timeframe: str = ""  # "today", "on Tuesday", "this week", ""
    meal_word: str | None = None  # "lunch"
    text: str = "*"  # free-text part for Typesense `q`


def _day_bounds(d_local: datetime) -> tuple[datetime, datetime]:
    start = d_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def make_plan(q: str, now: datetime) -> Plan:
    ql = q.lower().replace("’", "'")
    topic = next((t for t in ("med", "meal", "drink") if _has(ql, WORDS[t])), None)

    if re.search(r"\bhow (much|many)\b|\btotal\b|\bml\b", ql):
        intent: Intent = "amount"
    elif _has(ql, WORDS["missed"]):
        intent = "missed"
    elif re.search(r"\bwhat did (she|mum|mom|he)\b.*\b(say|said)\b|\bwhat .*\bsaid\b", ql):
        intent = "said"
    elif re.search(r"\b(last|latest|most recent)\b", ql) and re.search(r"\bwhen\b", ql):
        intent = "last"
    elif re.search(r"^(did|has|have|was|is|does)\b", ql.strip()):
        intent = "yesno"
    elif re.search(r"\bwhen\b", ql):
        intent = "last"
    else:
        intent = "list"

    if intent == "missed":
        kinds = ["missed"]
    elif _has(ql, WORDS["refused"]) or intent == "said":
        kinds = ["refused"] if intent != "said" or _has(ql, WORDS["refused"]) else ["refused", "concern"]
    elif _has(ql, WORDS["concern"]) and not topic:
        kinds = ["concern"]
    elif topic:
        kinds = [topic] if intent == "amount" else [topic, "refused", "missed"]
    else:
        kinds = []

    plan = Plan(intent=intent, topic=topic, kinds=kinds)

    meal_word = next((w for w in MEAL_DAYPART if re.search(rf"\b{w}\b", ql)), None)
    if meal_word:
        plan.meal_word = meal_word
        plan.dayparts = [MEAL_DAYPART[meal_word]]
    for dp in ("morning", "afternoon", "evening", "night"):
        if re.search(rf"\b{dp}\b", ql) and dp not in plan.dayparts:
            plan.dayparts.append(dp)
    if re.search(r"\btonight\b", ql):
        plan.dayparts = ["evening", "night"]

    n_local = to_local(now)
    if re.search(r"\b(today|so far|this (morning|afternoon|evening))\b|\btonight\b", ql):
        plan.start, plan.end = _day_bounds(n_local)
        plan.timeframe = "today"
    elif re.search(r"\byesterday\b|\blast night\b", ql):
        plan.start, plan.end = _day_bounds(n_local - timedelta(days=1))
        plan.timeframe = "yesterday"
    elif re.search(r"\bthis week\b", ql):
        start, _ = _day_bounds(n_local - timedelta(days=n_local.weekday()))
        plan.start, plan.end = start, _day_bounds(n_local)[1]
        plan.timeframe = "this week"
    elif re.search(r"\blast week\b", ql):
        this_mon, _ = _day_bounds(n_local - timedelta(days=n_local.weekday()))
        plan.start, plan.end = this_mon - timedelta(days=7), this_mon
        plan.timeframe = "last week"
    else:
        for i, wd in enumerate(WEEKDAYS):
            if re.search(rf"\b{wd}\b", ql):
                back = (n_local.weekday() - i) % 7
                plan.start, plan.end = _day_bounds(n_local - timedelta(days=back))
                plan.timeframe = "today" if back == 0 else f"on {wd.capitalize()}"
                break
    if plan.start is not None and plan.end is not None and plan.end > n_local + timedelta(minutes=1):
        plan.end = n_local + timedelta(minutes=1)
    if plan.kinds == ["refused"] and topic:
        plan.text = topic_word(topic)
    elif meal_word:
        plan.text = meal_word
    return plan


def topic_word(topic: str) -> str:
    return {"drink": "drink", "meal": "meal", "med": "tablets"}[topic]


# ---------------------------------------------------------------------------- matching (fallback corpus)


def _event_text(e: HearthEvent) -> str:
    return " ".join(x for x in (e.kind, e.summary, e.evidence, e.said or "") if x).lower()


def matches(e: HearthEvent, plan: Plan) -> bool:
    if plan.kinds and e.kind not in plan.kinds:
        return False
    if plan.start is not None and e.ts < plan.start:
        return False
    if plan.end is not None and e.ts >= plan.end:
        return False
    if plan.dayparts and daypart(e.ts) not in plan.dayparts:
        return False
    if e.kind == "refused" and plan.topic and plan.kinds == ["refused"]:
        # Refusals of a drink should mention a drink word (fallback has no synonym engine).
        words = WORDS[plan.topic] + SYNONYMS.get({"drink": "fluids", "med": "meds", "meal": "food"}[plan.topic], [])
        if not _has(_event_text(e), words):
            return False
    return True


def keyword_hits(q: str, now: datetime, recent: list[HearthEvent]) -> tuple[Plan, list[HearthEvent]]:
    plan = make_plan(q, now)
    found = sorted((e for e in recent if matches(e, plan)), key=lambda e: e.ts, reverse=True)
    if not found and not plan.kinds and plan.start is None:
        terms = [t for t in re.findall(r"[a-z']{3,}", q.lower()) if t not in _STOP]
        found = sorted((e for e in recent if any(t in _event_text(e) for t in terms)), key=lambda e: e.ts,
                       reverse=True)
    return plan, found


_STOP = {"did", "she", "her", "mum", "mom", "the", "and", "any", "what", "when", "how", "has", "have", "was",
         "this", "that", "with", "about", "today", "much", "many", "take", "last"}


# ---------------------------------------------------------------------------- answers


def snippet(e: HearthEvent) -> str:
    s = e.summary
    if e.said:
        s += f' (she said: "{e.said}")'
    elif e.evidence:
        s += f". {e.evidence}"
    return s[:240]


def _when(e: HearthEvent, now: datetime) -> str:
    return f"{time_label(e.ts)} {day_phrase(e.ts, now)}"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _sentence(s: str) -> str:
    s = s.strip().rstrip(".")
    return s[:1].upper() + s[1:] + "."


def compose_answer(q: str, plan: Plan, events: list[HearthEvent], now: datetime) -> str:
    """One or two plain sentences, built only from `events` (newest first)."""
    tf = plan.timeframe
    tf_txt = f" {tf}" if tf else ""
    nothing = "I couldn't find anything about that in the log."
    if plan.intent == "amount":
        if plan.topic in (None, "drink"):
            drinks = [e for e in events if e.kind == "drink"]
            if not drinks:
                return f"I couldn't find any drinks{tf_txt} in the log."
            total = sum(e.volume_ml_est or 0 for e in drinks)
            when = " so far today" if tf == "today" else tf_txt
            return f"About {total} ml{when}, across {_plural(len(drinks), 'drink')}."
        rows = [e for e in events if e.kind == plan.topic]
        if not rows:
            return nothing
        label = {"meal": ("meal", "meals"), "med": ("medication check-in", "medication check-ins")}[plan.topic]
        return f"{_plural(len(rows), *label)}{tf_txt} in the log. The latest was at {_when(rows[0], now)}."

    if not events:
        if plan.intent == "missed":
            return f"I couldn't find any missed check-ins{tf_txt}."
        return nothing

    if plan.intent == "missed":
        rows = [e for e in events if e.kind == "missed"] or events
        times = ", ".join(f"{weekday(e.ts)} {time_label(e.ts)}" for e in sorted(rows, key=lambda e: e.ts))
        return f"Yes, {_plural(len(rows), 'missed check-in')}{tf_txt}: {times}."

    if plan.intent == "said":
        rows = [e for e in events if e.said]
        if not rows:
            return "I couldn't find anything she said about that in the log."
        e = rows[0]
        return f'At {_when(e, now)} she said: "{e.said}".'

    if plan.intent == "last":
        e = events[0]
        tail = f' She said: "{e.said}".' if e.said else ""
        return f"The last time was {_when(e, now)}: {e.summary.rstrip('.')}.{tail}"

    if plan.intent == "yesno":
        positive = [e for e in events if plan.topic and e.kind == plan.topic] if plan.topic else \
            [e for e in events if e.kind in ("drink", "meal", "med")]
        if plan.meal_word:
            exact = [e for e in positive if plan.meal_word in _event_text(e)]
            positive = exact or positive
        if positive:
            e = positive[0]
            return f"Yes. {_sentence(e.summary)[:-1]} at {_when(e, now)}."
        negative = [e for e in events if e.kind in ("refused", "missed")]
        if negative:
            e = negative[0]
            tail = f' She said: "{e.said}".' if e.said else ""
            return f"Not that I can see. At {_when(e, now)}: {e.summary.rstrip('.')}.{tail}"
        return nothing

    e = events[0]
    return f"I found {_plural(len(events), 'entry', 'entries')}{tf_txt}. The latest, at {_when(e, now)}: " \
           f"{e.summary.rstrip('.')}."


def to_hits(events: list[HearthEvent], snippets: dict[str, str] | None = None) -> list[AskHit]:
    snippets = snippets or {}
    return [AskHit(event=e, snippet=snippets.get(e.id) or snippet(e)) for e in events[:10]]


__all__ = [
    "SYNONYMS",
    "Plan",
    "compose_answer",
    "day_str",
    "keyword_hits",
    "local_tz",
    "make_plan",
    "matches",
    "to_hits",
]
