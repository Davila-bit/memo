"""DigestWriter: three short, warm sentences for the daughter about one day.

Gemini (`GEMINI_TEXT_MODEL`) writes from the structured, already-redacted events plus totals computed
here. Its text is validated (length, no exclamation marks, no medical words, no numbers that are not in
the input); anything off falls back to a deterministic template.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from hearth_contracts.models import Digest, HearthEvent

from . import gemini
from ._common import gemini_key, gemini_text_model, time_label, to_local, warn_once

BANNED = re.compile(
    r"\b(diagnos\w*|dehydrat\w*|dose|dosage|overdose|infection|uti|dementia|alzheimer\w*|symptom\w*|"
    r"prescri\w*|doctor|nurse|hospital|medical|clinical|urgent\w*|emergency)\b",
    re.IGNORECASE,
)

SYSTEM = """You write a short end-of-day note for a woman about her elderly mother, from a check-in log.
Write exactly three short, plain, warm sentences: what went well; what didn't (or that nothing was missed);
at most one gentle, practical suggestion (for example offering a favourite drink earlier). No medical advice,
no diagnosis, no mention of doses, no exclamation marks, no emoji, no lists. Refer to the mother as the
given name. Use only numbers and times that appear in the input, written the same way. Under 60 words."""


@dataclass
class Totals:
    drinks: int
    fluid_ml: int
    meals: int
    meds: int
    refused: int
    missed: int
    concerns: int


def totals(events: list[HearthEvent]) -> Totals:
    k = [e.kind for e in events]
    return Totals(
        drinks=k.count("drink"),
        fluid_ml=sum(e.volume_ml_est or 0 for e in events if e.kind == "drink"),
        meals=k.count("meal"),
        meds=k.count("med"),
        refused=k.count("refused"),
        missed=k.count("missed"),
        concerns=k.count("concern"),
    )


def _rows(events: list[HearthEvent]) -> list[dict[str, str]]:
    rows = []
    for e in sorted(events, key=lambda e: e.ts):
        r = {"time": time_label(e.ts), "kind": e.kind, "summary": e.summary}
        if e.said:
            r["she_said"] = e.said
        rows.append(r)
    return rows


def _allowed_numbers(t: Totals, rows: list[dict[str, str]]) -> set[str]:
    nums = {str(v) for v in asdict(t).values()}
    for r in rows:
        nums.update(re.findall(r"\d+", r["time"]))
        nums.update(re.findall(r"\d+", r["summary"]))
        nums.update(re.findall(r"\d+", r.get("she_said", "")))
    return nums | {"0", "1", "2", "3"}  # "three sentences", "one drink", small counts written as words


def validate(text: str, allowed: set[str]) -> str | None:
    t = " ".join(text.split()).strip().strip('"')
    if not t or "!" in t or len(t.split()) > 60 or BANNED.search(t):
        return None
    if any(n not in allowed for n in re.findall(r"\d+", t.replace(",", ""))):
        return None
    if len(re.findall(r"[.?]", t)) < 2:
        return None
    return t


def _n(n: int, one: str, many: str | None = None) -> str:
    words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    w = words[n] if n < len(words) else str(n)
    return f"{w} {one if n == 1 else (many or one + 's')}"


def template(events: list[HearthEvent], her_name: str) -> str:
    t = totals(events)
    good: list[str] = []
    if t.drinks:
        good.append(f"{_n(t.drinks, 'drink')}, about {t.fluid_ml} ml")
    if t.meals:
        good.append(_n(t.meals, "meal"))
    if t.meds:
        good.append(f"{_n(t.meds, 'medication check-in')} observed")
    s1 = (f"{her_name} had {', '.join(good)}." if good else f"There were no confirmed check-ins for {her_name}.")
    s1 = s1[0].upper() + s1[1:]
    bad: list[str] = []
    refusals = [e for e in events if e.kind == "refused"]
    if refusals:
        r = refusals[-1]
        said = f', saying "{r.said}"' if r.said else ""
        bad.append(f"she said no at {time_label(r.ts)}{said}")
    if t.missed:
        bad.append(f"she missed {_n(t.missed, 'check-in')}")
    if t.concerns:
        bad.append("one check-in noticed something unusual, shown in the alerts" if t.concerns == 1
                   else f"{_n(t.concerns, 'check-in')} noticed something unusual, shown in the alerts")
    s2 = "Nothing was missed." if not bad else "; ".join(bad)[0].upper() + "; ".join(bad)[1:] + "."
    if refusals or t.missed:
        s3 = "A quick call in the afternoon might help her keep going."
    elif t.drinks and t.fluid_ml < 800:
        s3 = "Offering a favourite drink a little earlier might help."
    else:
        s3 = "A good day, nothing to do."
    return f"{s1} {s2} {s3}"


class GeminiDigestWriter:
    """Implements ports.DigestWriter. Never raises."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], Digest] = {}
        if not gemini_key():
            warn_once("digest", "GEMINI_API_KEY missing: daily digest uses the template")

    async def write(self, day: str, events: list[HearthEvent], her_name: str) -> Digest:
        try:
            day_events = [e for e in events if to_local(e.ts).strftime("%Y-%m-%d") == day] or events
            payload = json.dumps([e.model_dump(mode="json") for e in day_events], sort_keys=True)
            key = (day, hashlib.sha256((her_name + payload).encode()).hexdigest()[:16])
            if key in self._cache:
                return self._cache[key]
            digest = await self._write(day, day_events, her_name)
            self._cache[key] = digest
            return digest
        except Exception:  # noqa: BLE001
            return Digest(day=day, text=template(events, her_name), generated_by="template")

    async def _write(self, day: str, events: list[HearthEvent], her_name: str) -> Digest:
        t = totals(events)
        rows = _rows(events)
        if gemini_key() and events:
            prompt = json.dumps({"name": her_name, "day": day, "totals": asdict(t), "events": rows}, indent=1)
            raw = await gemini.generate(prompt, system=SYSTEM, timeout_s=15.0, temperature=0.4)
            ok = validate(raw or "", _allowed_numbers(t, rows))
            if ok:
                return Digest(day=day, text=ok, generated_by=gemini_text_model())
        return Digest(day=day, text=template(events, her_name), generated_by="template")
