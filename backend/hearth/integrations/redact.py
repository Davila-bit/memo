"""Redactor: strip names, phones, emails, addresses etc. from evidence / `said` text before storage.

Presidio (analyzer + anonymizer, spaCy `en_core_web_sm`) loads lazily in a background thread. Until it
is ready, or if it can't load, a regex pass covers phones, emails, street addresses, SSNs, cards,
dates of birth and the family deny-list (`HEARTH_REDACT_NAMES`).
"""

from __future__ import annotations

import re
import threading
import time

from ._common import env, log, set_status

# Everyday words the small NER model mislabels as PERSON / LOCATION in check-in sentences.
ALLOW_WORDS = {
    "tea", "mug", "cup", "glass", "water", "juice", "milk", "coffee", "toast", "soup", "sandwich",
    "pill", "pills", "tablet", "tablets", "pillbox", "pill box", "slot", "plate", "kitchen", "table",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "morning", "afternoon", "evening", "night", "today", "yesterday", "tonight",
    "mum", "mom", "mother", "dad", "helper", "sarah's helper", "nan", "gran", "grandma",
    "blue", "red", "green", "yellow", "white", "the blue mug", "the kitchen", "next door",
    "mrs", "mr", "ms", "dr", "ipad", "facetime",
}

TAGS = {
    "PERSON": "[name]",
    "PHONE_NUMBER": "[phone]",
    "EMAIL_ADDRESS": "[email]",
    "LOCATION": "[place]",
    "STREET_ADDRESS": "[address]",
    "US_SSN": "[id number]",
    "CREDIT_CARD": "[card]",
    "DATE_OF_BIRTH": "[date of birth]",
    "FAMILY_NAME": "[name]",
}

_STREET_SUFFIX = (
    r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Court|Ct|Boulevard|Blvd|Way|Place|Pl|Terrace|"
    r"Circle|Cir|Parkway|Pkwy|Highway|Hwy|Close|Crescent)"
)
RE_STREET = re.compile(rf"\b\d{{1,6}}\s+(?:[A-Z][a-zA-Z]+\.?\s+){{1,3}}{_STREET_SUFFIX}\b\.?(?:,?\s*(?:Apt|Unit|#)\s*\w+)?")
RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
RE_PHONE = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")
RE_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
RE_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")
RE_DOB = re.compile(
    r"\b(?:born(?:\s+on)?|birthday(?:\s+is)?|DOB|date of birth)[:\s]+"
    r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|[A-Z][a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})",
    re.IGNORECASE,
)
RE_FULL_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)\d{2}\b")


def deny_names() -> list[str]:
    raw = env("HEARTH_REDACT_NAMES", "") or ""
    return [n.strip() for n in raw.split(",") if len(n.strip()) >= 2]


def _deny_regex() -> re.Pattern[str] | None:
    names = deny_names()
    if not names:
        return None
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"\b(?:{alts})(?:'s)?\b", re.IGNORECASE)


def regex_redact(text: str) -> str:
    out = text
    deny = _deny_regex()
    if deny:
        out = deny.sub("[name]", out)
    out = RE_EMAIL.sub("[email]", out)
    out = RE_DOB.sub("[date of birth]", out)
    out = RE_SSN.sub("[id number]", out)
    out = RE_STREET.sub("[address]", out)
    out = RE_CARD.sub(lambda m: "[card]" if len(re.sub(r"\D", "", m.group(0))) >= 13 else m.group(0), out)
    out = RE_PHONE.sub("[phone]", out)
    out = RE_FULL_DATE.sub("[date]", out)
    return out


_KEEP_PREFIX = re.compile(r"(?:said|says|told|asked|called|rang|saw|mrs\.?|mr\.?|ms\.?|dr\.?|auntie|uncle)\s+",
                          re.IGNORECASE)


class PresidioRedactor:
    """Implements ports.Redactor. Never raises: worst case returns the regex-redacted text."""

    def __init__(self, *, eager: bool = True) -> None:
        self._analyzer = None
        self._anonymizer = None
        self._ready = threading.Event()
        self._failed = False
        self.load_seconds: float | None = None
        set_status("presidio", "mock")
        if eager:
            threading.Thread(target=self._load, name="presidio-load", daemon=True).start()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def wait_ready(self, timeout: float = 60.0) -> bool:
        return self._ready.wait(timeout)

    def _load(self) -> None:
        t0 = time.perf_counter()
        try:
            from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            from presidio_anonymizer import AnonymizerEngine

            provider = NlpEngineProvider(nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            })
            analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
            analyzer.registry.add_recognizer(PatternRecognizer(
                supported_entity="STREET_ADDRESS", name="street_address",
                patterns=[Pattern("street", RE_STREET.pattern, 0.85)],
            ))
            analyzer.registry.add_recognizer(PatternRecognizer(
                supported_entity="DATE_OF_BIRTH", name="date_of_birth",
                patterns=[Pattern("dob", RE_DOB.pattern, 0.9), Pattern("full_date", RE_FULL_DATE.pattern, 0.6)],
            ))
            names = deny_names()
            if names:
                # The deny-list recognizer matches whole tokens, so add the possessive forms too.
                analyzer.registry.add_recognizer(PatternRecognizer(
                    supported_entity="FAMILY_NAME", name="family_names",
                    deny_list=names + [f"{n}'s" for n in names] + [n.lower() for n in names],
                ))
            self._analyzer = analyzer
            self._anonymizer = AnonymizerEngine()
            self._redact_presidio("warm up: Held up the blue mug and called Mary at 404-555-0101 or mary@example.com.")
            self.load_seconds = time.perf_counter() - t0
            self._ready.set()
            set_status("presidio", "ok")
            log.info("presidio ready in %.1fs", self.load_seconds)
        except Exception as e:  # noqa: BLE001
            self._failed = True
            set_status("presidio", "error")
            log.warning("presidio unavailable, using regex redaction: %s", type(e).__name__)

    _ENTITIES: tuple[str, ...] = ("PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "LOCATION", "US_SSN", "CREDIT_CARD",
                 "STREET_ADDRESS", "DATE_OF_BIRTH", "FAMILY_NAME")

    def _redact_presidio(self, text: str) -> str:
        from presidio_anonymizer.entities import OperatorConfig

        assert self._analyzer is not None and self._anonymizer is not None
        results = self._analyzer.analyze(
            text=text, language="en", entities=list(self._ENTITIES),
            allow_list=sorted(ALLOW_WORDS), allow_list_match="exact",
        )
        kept = []
        for r in results:
            span = text[r.start:r.end].strip().lower()
            if span in ALLOW_WORDS or span.removesuffix("'s") in ALLOW_WORDS:
                continue
            if r.entity_type in ("PERSON", "LOCATION") and r.score < 0.6:
                continue
            if r.entity_type == "PERSON":
                # Keep a leading verb or title the NER swallowed: "Said Sarah" -> "Said [name]".
                m = _KEEP_PREFIX.match(text[r.start:r.end])
                if m and m.end() < r.end - r.start:
                    r.start += m.end()
            kept.append(r)
        ops = {k: OperatorConfig("replace", {"new_value": v}) for k, v in TAGS.items()}
        return self._anonymizer.anonymize(text=text, analyzer_results=kept, operators=ops).text

    def redact(self, text: str) -> str:
        if not text:
            return text
        try:
            if self._ready.is_set():
                # Presidio first, then the regex pass as a belt-and-braces for anything it missed.
                return regex_redact(self._redact_presidio(text))
        except Exception as e:  # noqa: BLE001
            log.warning("presidio redact failed, using regex: %s", type(e).__name__)
        try:
            return regex_redact(text)
        except Exception:  # noqa: BLE001
            return "[redacted]"


class RegexRedactor:
    def redact(self, text: str) -> str:
        try:
            return regex_redact(text) if text else text
        except Exception:  # noqa: BLE001
            return "[redacted]"
