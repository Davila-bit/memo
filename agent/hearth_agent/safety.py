"""Deterministic keyword backup for flag_concern (docs/08 §2). Not fall/seizure/stroke detection:
it only notices words she SAYS, so a concern is never missed because the model hesitated."""

from __future__ import annotations

import re

from hearth_contracts import ConcernCategory

# (pattern, category). Order matters: the first match wins.
_RULES: list[tuple[re.Pattern[str], ConcernCategory]] = [
    (re.compile(r"\b(i\s+)?(fell|fallen|have\s+had\s+a\s+fall|had\s+a\s+fall)\b"), "said_fell_or_hurt"),
    (re.compile(r"\bcan(no|')?t\s+get\s+up\b|\bcannot\s+get\s+up\b"), "said_fell_or_hurt"),
    (re.compile(r"\bon\s+the\s+floor\b"), "said_fell_or_hurt"),
    (re.compile(r"\bhelp\s+me\b|\bplease\s+help\b|\bi\s+need\s+help\b"), "asked_for_help"),
    (re.compile(r"\bcan(no|')?t\s+breathe\b|\bcannot\s+breathe\b"), "reports_pain_or_unwell"),
    (re.compile(r"\bchest\b"), "reports_pain_or_unwell"),
    (re.compile(r"\b(numb|weak|dizzy)\b"), "reports_pain_or_unwell"),
    (re.compile(r"\b(hurts?|hurting|i'?m\s+hurt)\b"), "said_fell_or_hurt"),
]


def _norm(text: str) -> str:
    return text.lower().replace("’", "'").replace("‘", "'")


def match_concern(text: str) -> ConcernCategory | None:
    t = _norm(text)
    for pat, cat in _RULES:
        if pat.search(t):
            return cat
    return None
