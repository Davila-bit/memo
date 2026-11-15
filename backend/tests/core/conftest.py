from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hearth.core.app import create_app
from hearth.core.settings import Settings

PASS = "test-pass"
TOKEN = "test-internal"


def make_settings(tmp_path: Path, **kw) -> Settings:
    s = Settings(passcode=PASS, session_secret="test-secret", internal_token=TOKEN, scheduler=False,
                 mock_scale=0.02, retry_seconds=0.1, tick_seconds=0.05, media_dir=tmp_path / "media")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def _make(login: bool = True, **kw) -> TestClient:
        c = TestClient(create_app(make_settings(tmp_path, **kw)))
        c.__enter__()
        clients.append(c)
        if login:
            r = c.post("/api/auth/login", json={"passcode": PASS})
            assert r.status_code == 200, r.text
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def wait_for(fn: Callable[[], object], timeout: float = 5.0, every: float = 0.03):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting; last value {last!r}")


def wait_ended(c: TestClient, checkin_id: str, timeout: float = 5.0) -> dict:
    def done():
        r = c.get(f"/api/checkins/{checkin_id}").json()
        return r if r["status"] == "ended" else None

    return wait_for(done, timeout)


def internal(c: TestClient, checkin_id: str, tool: str, body: dict, token: str = TOKEN):
    return c.post(f"/internal/checkins/{checkin_id}/{tool}", json=body, headers={"X-Hearth-Token": token})
