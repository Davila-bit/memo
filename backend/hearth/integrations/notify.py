"""Notifier: Web Push to the daughter's PWA, plus ntfy for urgent alerts.

Web Push payload (JSON string the service worker receives in `event.data.json()`):
    {"title": str, "body": str, "url": str, "urgent": bool,
     "actions": [{"action": str, "title": str}], "tag": str, "ts": str}
`url` is a same-origin path to open on click. `actions[].action` values are `AlertAction` ids
("handling", "called_911", "retry_later", "i_will_call", "ask_neighbour") or "open".

ntfy (urgent always; everything when NTFY_ALL=1) uses JSON publishing to `NTFY_URL`, so non-ASCII titles
need no header encoding. The topic is a capability: it is never logged.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from hearth_contracts.models import Alert, PushSubscription

from ._common import env, log, set_status, time_label, warn_once

DEFAULT_SUBJECT = "https://github.com/Davila-bit/hearth-home"

ALERT_ACTIONS: dict[str, list[tuple[str, str]]] = {
    "concern": [("handling", "I'm handling it"), ("called_911", "Call 911")],
    "refused": [("retry_later", "Try again later"), ("i_will_call", "I'll call her")],
    "no_answer": [("i_will_call", "I'll call her"), ("ask_neighbour", "Ask a neighbour")],
}


def public_url() -> str:
    # TODO(contract): HEARTH_PUBLIC_URL (the web app's public origin) is not in API.md §3 yet.
    return (env("HEARTH_PUBLIC_URL", "http://localhost:3000") or "").rstrip("/")


def alert_message(alert: Alert) -> tuple[str, str]:
    """Title + body with the docs/08 §2 wording for concerns."""
    title = alert.title
    if alert.kind == "concern":
        if alert.said:
            body = (f"The check-in noticed something unusual: she said '{alert.said}' at {time_label(alert.ts)}. "
                    "Please check on her now.")
        else:
            body = f"The check-in noticed something unusual at {time_label(alert.ts)}. Please check on her now."
        if alert.detail and alert.detail not in body:
            body = f"{body} {alert.detail}"
    else:
        body = alert.detail
    return title, body


def push_payload(title: str, body: str, *, urgent: bool, url: str | None, actions: list[tuple[str, str]] | None,
                 tag: str | None = None) -> dict:
    return {
        "title": title,
        "body": body,
        "url": url or "/",
        "urgent": urgent,
        "actions": [{"action": a, "title": t} for a, t in (actions or [])][:2],
        "tag": tag or ("hearth-urgent" if urgent else "hearth"),
        "ts": datetime.now(UTC).isoformat(),
    }


def ntfy_body(topic: str, title: str, body: str, *, urgent: bool, url: str | None,
              actions: list[tuple[str, str]] | None) -> dict:
    base = public_url()
    click = (url if url and url.startswith(("http://", "https://")) else f"{base}{url or '/'}")
    view_actions = []
    for a, label in (actions or [])[:3]:
        target = "tel:911" if a == "called_911" else f"{click}{'&' if '?' in click else '?'}action={a}"
        view_actions.append({"action": "view", "label": label, "url": target, "clear": a != "called_911"})
    return {
        "topic": topic,
        "title": title,
        "message": body,
        "priority": 5 if urgent else 3,
        "tags": ["warning", "house"] if urgent else ["house"],
        "click": click,
        "actions": view_actions,
    }


class HearthNotifier:
    """Implements ports.Notifier. Never raises."""

    def __init__(self, get_subscriptions: Callable[[], Awaitable[list[PushSubscription]]], *,
                 http: httpx.AsyncClient | None = None, webpush_fn: Callable | None = None) -> None:
        self._get_subs = get_subscriptions
        self._http = http
        self._webpush = webpush_fn
        self._dead: set[str] = set()
        self.vapid_private = env("VAPID_PRIVATE_KEY")
        self.vapid_public = env("VAPID_PUBLIC_KEY")
        self.vapid_subject = env("VAPID_SUBJECT", DEFAULT_SUBJECT) or DEFAULT_SUBJECT
        if self.vapid_subject and not self.vapid_subject.startswith(("mailto:", "https://")):
            self.vapid_subject = DEFAULT_SUBJECT
        self.ntfy_url = (env("NTFY_URL", "https://ntfy.sh") or "https://ntfy.sh").rstrip("/")
        self.ntfy_topic = env("NTFY_TOPIC")
        self.ntfy_all = env("NTFY_ALL", "0") == "1"
        set_status("web_push", "ok" if self.vapid_private and self.vapid_public else "missing_key")
        set_status("ntfy", "ok" if self.ntfy_topic else "missing_key")
        if not (self.vapid_private and self.vapid_public):
            warn_once("vapid", "VAPID keys missing: Web Push disabled (in-app alerts still show)")
        if not self.ntfy_topic:
            warn_once("ntfy", "NTFY_TOPIC missing: no ntfy escalation (in-app alerts still show)")

    # ------------------------------------------------------------------ port

    async def notify(self, title: str, body: str, *, urgent: bool = False, url: str | None = None,
                     actions: list[tuple[str, str]] | None = None, tag: str | None = None) -> None:
        jobs = [self._push_all(push_payload(title, body, urgent=urgent, url=url, actions=actions, tag=tag))]
        if self.ntfy_topic and (urgent or self.ntfy_all):
            jobs.append(self._ntfy(title, body, urgent=urgent, url=url, actions=actions))
        try:
            await asyncio.gather(*jobs, return_exceptions=True)
        except Exception as e:  # noqa: BLE001
            log.warning("notify failed: %s", type(e).__name__)

    async def alert(self, alert: Alert) -> None:
        try:
            title, body = alert_message(alert)
            await self.notify(title, body, urgent=alert.tier == "urgent", url=f"/alerts/{alert.id}",
                              actions=ALERT_ACTIONS.get(alert.kind), tag=f"alert-{alert.id}")
        except Exception as e:  # noqa: BLE001
            log.warning("alert notify failed: %s", type(e).__name__)

    def pop_dead_endpoints(self) -> list[str]:
        """Subscriptions the push service said are gone (404/410). The core should delete them."""
        dead, self._dead = sorted(self._dead), set()
        return dead

    # ------------------------------------------------------------------ Web Push

    async def _push_all(self, payload: dict) -> None:
        if not (self.vapid_private and self.vapid_public):
            return
        try:
            subs = await self._get_subs()
        except Exception as e:  # noqa: BLE001
            log.warning("could not load push subscriptions: %s", type(e).__name__)
            return
        if not subs:
            return
        data = json.dumps(payload)
        results = await asyncio.gather(*(asyncio.to_thread(self._push_one, s, data) for s in subs),
                                       return_exceptions=True)
        failed = sum(1 for r in results if r is not True)
        set_status("web_push", "ok" if failed < len(subs) or not subs else "error")
        if failed:
            log.warning("web push: %d of %d deliveries failed", failed, len(subs))

    def _push_one(self, sub: PushSubscription, data: str) -> bool:
        fn = self._webpush
        if fn is None:
            from pywebpush import webpush as fn
        try:
            fn(subscription_info={"endpoint": sub.endpoint, "keys": sub.keys}, data=data,
               vapid_private_key=self.vapid_private, vapid_claims={"sub": self.vapid_subject},
               timeout=5, ttl=3600)
            return True
        except Exception as e:  # noqa: BLE001
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                self._dead.add(sub.endpoint)  # quietly: the browser unsubscribed
                return False
            log.warning("web push delivery failed (status %s)", status)
            return False

    # ------------------------------------------------------------------ ntfy

    async def _ntfy(self, title: str, body: str, *, urgent: bool, url: str | None,
                    actions: list[tuple[str, str]] | None) -> None:
        assert self.ntfy_topic
        payload = ntfy_body(self.ntfy_topic, title, body, urgent=urgent, url=url, actions=actions)
        try:
            client = self._http or httpx.AsyncClient(timeout=5)
            try:
                r = await client.post(self.ntfy_url, json=payload, timeout=5)
            finally:
                if self._http is None:
                    await client.aclose()
            if r.status_code >= 400:
                set_status("ntfy", "error")
                log.warning("ntfy publish failed (status %s)", r.status_code)
            else:
                set_status("ntfy", "ok")
        except Exception as e:  # noqa: BLE001
            set_status("ntfy", "error")
            log.warning("ntfy publish failed: %s", type(e).__name__)
