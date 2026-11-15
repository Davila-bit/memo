"""Passcode sign-in, signed session cookie (stdlib hmac), login rate limit, ws tickets, internal token."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, WebSocket

COOKIE = "hearth_session"
MAX_AGE = 30 * 24 * 3600


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Auth:
    def __init__(self, passcode: str, secret: str, internal_token: str) -> None:
        self.passcode = passcode
        self.secret = secret.encode()
        self.internal_token = internal_token
        self.attempts: dict[str, deque[float]] = defaultdict(deque)
        self.tickets: dict[str, float] = {}

    # ------------------------------------------------------------------ passcode + cookie
    def check_passcode(self, given: str) -> bool:
        return hmac.compare_digest(given.encode(), self.passcode.encode())

    def rate_limited(self, ip: str, limit: int = 5, window: float = 60.0) -> bool:
        now = time.monotonic()
        q = self.attempts[ip]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False

    def sign(self, user: str = "sarah") -> str:
        payload = _b64(json.dumps({"u": user, "iat": int(time.time())}).encode())
        sig = _b64(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())
        return f"{payload}.{sig}"

    def verify(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        payload, sig = token.rsplit(".", 1)
        want = _b64(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return False
        try:
            data = json.loads(_unb64(payload))
        except ValueError:
            return False
        return time.time() - int(data.get("iat", 0)) < MAX_AGE

    # ------------------------------------------------------------------ ws tickets
    def new_ticket(self) -> str:
        now = time.monotonic()
        for t, exp in list(self.tickets.items()):
            if exp < now:
                self.tickets.pop(t, None)
        t = secrets.token_urlsafe(24)
        self.tickets[t] = now + 60
        return t

    def use_ticket(self, t: str | None) -> bool:
        if not t:
            return False
        exp = self.tickets.pop(t, None)
        return exp is not None and exp >= time.monotonic()

    # ------------------------------------------------------------------ internal
    def check_internal(self, given: str | None) -> bool:
        return bool(given) and hmac.compare_digest(given.encode(), self.internal_token.encode())


def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


def client_ip(request: Request) -> str:
    # X-Forwarded-For is ignored on purpose: it is client-controlled, so trusting it would let
    # anyone dodge the login limit. Behind the Next.js proxy this makes the limit global.
    return request.client.host if request.client else "unknown"


def require_session(request: Request) -> None:
    auth: Auth = request.app.state.auth
    if not auth.verify(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="Please sign in first.")


def require_internal(request: Request) -> None:
    auth: Auth = request.app.state.auth
    if not auth.check_internal(request.headers.get("x-hearth-token")):
        raise HTTPException(status_code=401, detail="This endpoint is for the Memo helper only.")


def ws_allowed(ws: WebSocket) -> bool:
    auth: Auth = ws.app.state.auth
    return auth.verify(ws.cookies.get(COOKIE)) or auth.use_ticket(ws.query_params.get("ticket"))
