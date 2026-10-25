"""Request-id propagation (pure ASGI) and per-app rate limiting."""

import re
import uuid

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from rootcause_api.envelope import REQUEST_ID_HEADER, fail
from rootcause_core.settings import Settings

REQUEST_ID_STATE_KEY = "request_id"
REQUEST_ID_LOG_KEY = "request_id"
RETRY_AFTER_SECONDS = "60"
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


def create_limiter(settings: Settings) -> Limiter:
    """Build a limiter owned by a single app so counters never leak across apps."""
    return Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])


def rate_limit_response(request: Request) -> JSONResponse:
    """429 envelope with the request id and a conservative ``Retry-After``."""
    return fail(
        "rate_limited",
        "Too many requests. Please retry later.",
        429,
        request_id=request_id_of(request),
        headers={"Retry-After": RETRY_AFTER_SECONDS},
    )


class RateLimitMiddleware:
    """Apply the app's default slowapi limit to every HTTP request.

    slowapi's own ``SlowAPIMiddleware`` resolves the route by scanning
    ``app.routes`` for objects with an ``endpoint`` attribute; FastAPI's
    ``include_router`` wraps routers in objects without one, so no route is
    found and nothing is limited. This middleware asks the limiter directly
    (url-keyed, default limits) and renders the envelope itself.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        limiter: Limiter = scope["app"].state.limiter
        try:
            # slowapi exposes no public "check this request" call.
            limiter._check_request_limit(request, None, True)
        except RateLimitExceeded:
            await rate_limit_response(request)(scope, receive, send)
            return
        await self.app(scope, receive, send)
