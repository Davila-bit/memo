"""Settings from the environment (contracts/API.md §3). Dev defaults log one warning each."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("hearth.core")

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() not in {"0", "false", "no", "off"}


@dataclass
class Settings:
    transport_default: str = "mock"
    passcode: str = "hearth-demo"
    session_secret: str = ""
    internal_token: str = "dev-internal"
    api_base: str = "http://127.0.0.1:8000"
    database_url: str | None = None
    cors_origins: list[str] = field(default_factory=list)
    retry_seconds: float = 120.0
    watch: bool = False
    scheduler: bool = True
    tick_seconds: float = 5.0
    mock_scale: float = 1.0  # mock call timeline speed (tests use ~0.02)
    media_dir: Path = BACKEND_DIR / "var" / "media"
    kiosk_device: str = "kitchen"
    vapid_public_key: str | None = None
    max_call_seconds: int = 90
    chert_hook_key: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        s = cls()
        t = env.get("HEARTH_TRANSPORT", "mock").strip() or "mock"
        s.transport_default = t if t in {"facetime", "kiosk", "mock"} else "mock"

        if env.get("HEARTH_PASSCODE"):
            s.passcode = env["HEARTH_PASSCODE"]
        else:
            log.warning("HEARTH_PASSCODE not set: using the dev passcode 'hearth-demo'")
        if env.get("HEARTH_SESSION_SECRET"):
            s.session_secret = env["HEARTH_SESSION_SECRET"]
        else:
            s.session_secret = secrets.token_hex(32)
            log.warning("HEARTH_SESSION_SECRET not set: using a random secret (sessions end on restart)")
        if env.get("HEARTH_INTERNAL_TOKEN"):
            s.internal_token = env["HEARTH_INTERNAL_TOKEN"]
        else:
            log.warning("HEARTH_INTERNAL_TOKEN not set: using the dev token 'dev-internal'")

        s.api_base = env.get("HEARTH_API_BASE", s.api_base) or s.api_base
        s.database_url = env.get("TIGER_DATABASE_URL") or None
        s.cors_origins = [o.strip() for o in env.get("HEARTH_CORS_ORIGINS", "").split(",") if o.strip()]
        try:
            s.retry_seconds = float(env.get("HEARTH_RETRY_SECONDS", "120"))
        except ValueError:
            s.retry_seconds = 120.0
        s.watch = env.get("HEARTH_WATCH") == "1"
        s.scheduler = _flag("HEARTH_SCHEDULER", True)
        try:
            s.mock_scale = float(env.get("HEARTH_MOCK_SCALE", "1"))
        except ValueError:
            s.mock_scale = 1.0
        if env.get("HEARTH_MEDIA_DIR"):
            s.media_dir = Path(env["HEARTH_MEDIA_DIR"])
        s.kiosk_device = env.get("HEARTH_KIOSK_DEVICE", "kitchen") or "kitchen"
        s.vapid_public_key = env.get("VAPID_PUBLIC_KEY") or None
        s.chert_hook_key = env.get("HEARTH_CHERT_HOOK_KEY") or None
        return s
