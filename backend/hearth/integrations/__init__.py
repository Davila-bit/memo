"""Notifier, Search, DigestWriter and Redactor implementations.

Factories read their own env (contracts/API.md §3), log one line when a key is missing, and fall back.
None of the returned objects raise out of their methods.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from hearth_contracts import ports
from hearth_contracts.models import PushSubscription

from ._common import STATUS, env, gemini_key, set_status, warn_once
from .digest import GeminiDigestWriter
from .notify import HearthNotifier
from .redact import PresidioRedactor, RegexRedactor
from .search import FallbackSearch, TypesenseSearch

__all__ = [
    "FallbackSearch",
    "GeminiDigestWriter",
    "HearthNotifier",
    "PresidioRedactor",
    "TypesenseSearch",
    "build_digest_writer",
    "build_notifier",
    "build_redactor",
    "build_search",
    "health",
]


def build_notifier(get_subscriptions: Callable[[], Awaitable[list[PushSubscription]]]) -> ports.Notifier:
    """Web Push (VAPID_*) + ntfy (NTFY_TOPIC). Extra: `.pop_dead_endpoints()` lists gone subscriptions."""
    return HearthNotifier(get_subscriptions)


def build_search() -> ports.Search:
    """Typesense when TYPESENSE_HOST + TYPESENSE_API_KEY are set, else keyword fallback over `recent`.
    Extras on both: `await .reindex(events) -> int`, `await .clear()`."""
    if env("TYPESENSE_HOST") and env("TYPESENSE_API_KEY"):
        set_status("typesense", "ok")
        return TypesenseSearch()
    warn_once("typesense", "TYPESENSE_HOST/TYPESENSE_API_KEY missing: ask-the-log uses keyword fallback")
    set_status("typesense", "missing_key")
    return FallbackSearch()


def build_digest_writer() -> ports.DigestWriter:
    set_status("gemini_text", "ok" if gemini_key() else "missing_key")
    return GeminiDigestWriter()


def build_redactor() -> ports.Redactor:
    """Presidio loads in a background thread (~0.6 s); regex redaction covers the gap.
    HEARTH_REDACTION=regex skips Presidio entirely."""
    if env("HEARTH_REDACTION") == "regex":
        set_status("presidio", "mock")
        return RegexRedactor()
    return PresidioRedactor()


def health() -> dict[str, str]:
    out = {
        "typesense": "ok" if env("TYPESENSE_HOST") and env("TYPESENSE_API_KEY") else "missing_key",
        "gemini_text": "ok" if gemini_key() else "missing_key",
        "web_push": "ok" if env("VAPID_PRIVATE_KEY") and env("VAPID_PUBLIC_KEY") else "missing_key",
        "ntfy": "ok" if env("NTFY_TOPIC") else "missing_key",
        "presidio": "mock",
    }
    for k, v in STATUS.items():
        if k in out and out[k] != "missing_key":
            out[k] = v
    return out
