"""The per-call time budget (AGENTS.md rule 3: every call ≤ 90 s)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("hearth.budget")


class BudgetTimer:
    """Starts at "live". Calls on_warn at max_seconds - warn_before, on_expire at max_seconds."""

    def __init__(self, max_seconds: float, on_warn: Callable[[], Awaitable[None]],
                 on_expire: Callable[[], Awaitable[None]], warn_before: float = 10.0) -> None:
        self.max_seconds = max_seconds
        self.warn_at = max(0.0, max_seconds - warn_before)
        self._on_warn = on_warn
        self._on_expire = on_expire
        self._task: asyncio.Task | None = None
        self.warned = False
        self.expired = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="hearth-budget")

    async def _run(self) -> None:
        await asyncio.sleep(self.warn_at)
        self.warned = True
        log.info("budget: goodbye warning at %.0fs", self.warn_at)
        try:
            await self._on_warn()
        except Exception:  # noqa: BLE001
            log.exception("budget warn callback failed")
        await asyncio.sleep(self.max_seconds - self.warn_at)
        self.expired = True
        log.info("budget: expired at %.0fs", self.max_seconds)
        await self._on_expire()

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
