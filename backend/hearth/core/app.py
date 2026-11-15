"""App factory: `uvicorn hearth.core.app:create_app --factory`.

Wires the store (Tiger or memory), demo clock, bus, CheckinService, schedule tick, and the
plug-in modules (hearth.calls, hearth.integrations) with built-in fallbacks when they are absent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .auth import Auth
from .checkins import CheckinService
from .clock import Bus, DemoClock
from .fallbacks import KeywordSearch, LogNotifier, MockCallService, RegexRedactor, TemplateDigest
from .routes import api, hooks, internal, live, public
from .seed import seed
from .settings import Settings
from .store import MemoryStore, Store

log = logging.getLogger("hearth.core")
STATUSES = {"ok", "missing_key", "error", "mock"}


class _WebhookAccessFilter(logging.Filter):
    """The webhook path contains a credential; Uvicorn normally prints the entire URL."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, version, status = record.args
            if isinstance(path, str) and path.startswith("/hooks/chert/"):
                record.args = (client, method, "/hooks/chert/<redacted>", version, status)
        return True


def _setup_logging() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _WebhookAccessFilter) for f in access.filters):
        access.addFilter(_WebhookAccessFilter())
    root = logging.getLogger("hearth")
    root.setLevel(logging.INFO)
    if not root.handlers and not logging.getLogger().handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)


def _status_of(obj: Any, fallback_types: tuple[type, ...]) -> str:
    if isinstance(obj, fallback_types):
        return "mock"
    fn = getattr(obj, "health", None)
    if callable(fn):
        try:
            v = fn()
        except Exception:  # noqa: BLE001
            return "error"
        if isinstance(v, str):
            return v if v in STATUSES else "error"
    return "ok"


def _build_integrations(get_subs) -> dict[str, Any]:
    out: dict[str, Any] = {}
    builders = {
        "notifier": ("build_notifier", (get_subs,), LogNotifier),
        "search": ("build_search", (), KeywordSearch),
        "digest": ("build_digest_writer", (), TemplateDigest),
        "redactor": ("build_redactor", (), RegexRedactor),
    }
    for key, (fn_name, args, fallback) in builders.items():
        try:
            import hearth.integrations as integ

            out[key] = getattr(integ, fn_name)(*args)
            log.info("integrations: %s from hearth.integrations.%s", key, fn_name)
        except (ImportError, AttributeError):
            log.info("integrations: %s not available; using the built-in %s", fn_name, fallback.__name__)
            out[key] = fallback()
        except Exception as e:  # noqa: BLE001
            log.warning("integrations: %s failed (%s); using the built-in %s", fn_name, e, fallback.__name__)
            out[key] = fallback()
    return out


def _build_calls() -> tuple[Any | None, Any | None]:
    service, router = None, None
    try:
        from hearth.calls import build_call_service

        service = build_call_service()
        log.info("calls: using hearth.calls.build_call_service")
    except (ImportError, AttributeError):
        log.info("calls: hearth.calls.build_call_service not available; mock transport only")
    except Exception as e:  # noqa: BLE001
        log.warning("calls: build_call_service failed (%s); mock transport only", e)
    try:
        from hearth.calls import router as calls_router

        router = calls_router
    except (ImportError, AttributeError):
        pass
    return service, router


async def _open_store(settings: Settings) -> tuple[Store, str]:
    if not settings.database_url:
        log.info("store: TIGER_DATABASE_URL not set; using the in-memory store")
        return MemoryStore(), "missing_key"
    try:
        from .pgstore import PgStore

        store = PgStore(settings.database_url)
        await store.open()
        log.info("store: Tiger Cloud (schema hearth)")
        return store, "ok"
    except Exception as e:  # noqa: BLE001
        log.warning("store: Tiger failed to open (%s); using the in-memory store", type(e).__name__)
        return MemoryStore(), "error"


def create_app(settings: Settings | None = None) -> FastAPI:
    _setup_logging()
    settings = settings or Settings.from_env()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    real_calls, calls_router = _build_calls()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store, tiger_status = await _open_store(settings)
        clock = DemoClock(store)
        await clock.load()
        if await store.is_empty():
            n = await seed(store, clock.now())
            log.info("store was empty: seeded Margaret and %d events", n)
        integ = _build_integrations(store.list_push_subscriptions)
        bus = Bus()
        mock_calls = MockCallService(scale=settings.mock_scale, max_seconds=settings.max_call_seconds)
        svc = CheckinService(store=store, clock=clock, bus=bus, settings=settings, notifier=integ["notifier"],
                             search=integ["search"], digest=integ["digest"], redactor=integ["redactor"],
                             real_calls=real_calls, mock_calls=mock_calls)
        app.state.svc = svc
        svc._bg(svc.reindex())
        app.state.fallback_search = KeywordSearch()
        app.state.fallback_digest = TemplateDigest()

        def health() -> dict[str, str]:
            h: dict[str, str] = {"tiger": tiger_status}
            if real_calls is None:
                h["calls"] = "mock"
            else:
                try:
                    for k, v in (real_calls.health() or {}).items():
                        h[k] = v if v in STATUSES else "error"
                except Exception:  # noqa: BLE001
                    h["calls"] = "error"
            h["notifier"] = _status_of(integ["notifier"], (LogNotifier,))
            h["search"] = _status_of(integ["search"], (KeywordSearch,))
            h["digest"] = _status_of(integ["digest"], (TemplateDigest,))
            h["redactor"] = _status_of(integ["redactor"], (RegexRedactor,))
            # hearth.integrations reports per-service detail (which key is missing); fold it in.
            try:
                import hearth.integrations as _integ

                detail_fn = getattr(_integ, "health", None)
                detail = dict(detail_fn() or {}) if callable(detail_fn) else {}
            except Exception:  # noqa: BLE001
                detail = {}
            detail = {k: (v if v in STATUSES else "error") for k, v in detail.items()}
            h.update(detail)
            if detail:
                if h["notifier"] == "ok":
                    push, ntfy = detail.get("web_push"), detail.get("ntfy")
                    h["notifier"] = "ok" if "ok" in (push, ntfy) else (push or ntfy or "ok")
                if h["search"] == "ok" and "typesense" in detail:
                    h["search"] = detail["typesense"]
                if h["digest"] == "ok" and "gemini_text" in detail:
                    h["digest"] = detail["gemini_text"]
            return h

        app.state.health = health
        sched: asyncio.Task | None = None
        if settings.scheduler:
            svc.reset_tick()
            sched = asyncio.create_task(svc.run_scheduler(), name="hearth-schedule")
        else:
            log.info("schedule: HEARTH_SCHEDULER=0, the schedule tick is off")
        try:
            yield
        finally:
            if sched:
                sched.cancel()
            await svc.shutdown()
            for obj in (real_calls, *integ.values()):
                closer = getattr(obj, "aclose", None) or getattr(obj, "close", None)
                if callable(closer):
                    with contextlib.suppress(Exception):
                        res = closer()
                        if asyncio.iscoroutine(res):
                            await res
            await store.close()

    app = FastAPI(title="Memo API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.auth = Auth(settings.passcode, settings.session_secret, settings.internal_token)
    app.state.real_calls = real_calls
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])
    app.include_router(public)
    app.include_router(hooks)
    app.include_router(api)
    app.include_router(internal)
    app.include_router(live)
    if calls_router is not None:
        app.include_router(calls_router)
        log.info("calls: included the hearth.calls router")
    app.mount("/media", StaticFiles(directory=str(settings.media_dir), check_dir=False), name="media")
    return app
