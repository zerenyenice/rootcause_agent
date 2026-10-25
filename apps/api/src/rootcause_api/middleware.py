"""Request-id propagation and per-client rate limiting (both pure ASGI)."""

import math
import re
import time
import uuid

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from limits import RateLimitItem
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from rootcause_api.envelope import REQUEST_ID_HEADER, fail
from rootcause_core.settings import Settings

REQUEST_ID_STATE_KEY = "request_id"
REQUEST_ID_LOG_KEY = "request_id"
UNKNOWN_CLIENT = "unknown"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
HSTS_HEADER = "Strict-Transport-Security"
HSTS_VALUE = "max-age=31536000; includeSubDomains"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


def request_id_of(request: Request) -> str | None:
    """Return the id assigned to ``request`` by :class:`RequestIdMiddleware`."""
    value: object = request.scope.get("state", {}).get(REQUEST_ID_STATE_KEY)
    return value if isinstance(value, str) else None


def _incoming_request_id(scope: Scope) -> str:
    """Reuse a well-formed inbound id, otherwise mint a UUID4."""
    for name, value in scope["headers"]:
        if bytes(name).decode("latin-1").lower() == REQUEST_ID_HEADER.lower():
            candidate = bytes(value).decode("latin-1")
            if _VALID_REQUEST_ID.fullmatch(candidate):
                return candidate
    return str(uuid.uuid4())


class RequestIdMiddleware:
    """Assign ``X-Request-ID``, bind it into the log context, echo it on responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _incoming_request_id(scope)
        scope.setdefault("state", {})[REQUEST_ID_STATE_KEY] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        structlog.contextvars.bind_contextvars(**{REQUEST_ID_LOG_KEY: request_id})
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            structlog.contextvars.unbind_contextvars(REQUEST_ID_LOG_KEY)


class SecurityHeadersMiddleware:
    """Add conservative response headers; HSTS only when ``hsts`` (prod) is set."""

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.headers = {**SECURITY_HEADERS, **({HSTS_HEADER: HSTS_VALUE} if hsts else {})}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    response_headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_limiter(settings: Settings) -> MovingWindowRateLimiter:
    """Build a limiter owned by a single app so counters never leak across apps."""
    return MovingWindowRateLimiter(MemoryStorage())


def client_key(scope: Scope) -> str:
    """Peer address from the ASGI scope; ``X-Forwarded-For`` is deliberately ignored.

    Behind a reverse proxy run uvicorn with ``--proxy-headers`` and
    ``--forwarded-allow-ips`` so the server rewrites ``scope["client"]`` from
    trusted proxies only; a client-supplied header can then never pick its bucket.
    """
    client = scope.get("client")
    return str(client[0]) if client else UNKNOWN_CLIENT


def retry_after_seconds(reset_time: float, now: float) -> int:
    """Whole seconds until the window frees a slot, never below one."""
    return max(1, math.ceil(reset_time - now))


def rate_limit_response(request: Request, retry_after: int) -> JSONResponse:
    """429 envelope with the request id and a ``Retry-After`` from the window reset."""
    return fail(
        "rate_limited",
        "Too many requests. Please retry later.",
        429,
        request_id=request_id_of(request),
        headers={"Retry-After": str(retry_after)},
    )


class RateLimitMiddleware:
    """One global moving-window bucket per client IP, across all paths."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        app = scope["app"]
        limiter: MovingWindowRateLimiter = app.state.limiter
        item: RateLimitItem = app.state.rate_limit_item
        key = client_key(scope)
        if not limiter.hit(item, key):
            stats = limiter.get_window_stats(item, key)
            retry_after = retry_after_seconds(stats.reset_time, time.time())
            await rate_limit_response(Request(scope), retry_after)(scope, receive, send)
            return
        await self.app(scope, receive, send)
