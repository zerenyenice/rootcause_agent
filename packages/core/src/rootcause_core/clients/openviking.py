"""OpenViking HTTP client (v0.3.x authenticates with the ``X-API-Key`` header).

Lifecycle:

* The cached :class:`httpx.AsyncClient` belongs to the event loop that first
  sends a request through it (its connection pool is bound to that loop). Use
  it from that loop only; the API process has exactly one.
* The API lifespan (T035) owns shutdown and must ``await close_clients()``;
  nothing closes the client implicitly.
* One client is cached per :class:`Settings` instance. A different ``Settings``
  gets its own client instead of replacing the existing one, so no client is
  ever orphaned: every cached client stays tracked until ``close_clients()``
  closes it. Closing the old client eagerly is deliberately not done because
  :func:`openviking_http` is synchronous (it cannot ``await aclose()``) and a
  fire-and-forget close task could cancel requests still in flight on the old
  client. In production :func:`~rootcause_core.settings.get_settings` is
  cached, so there is a single client; extra entries only appear in tests.
"""

import httpx

from rootcause_core.clients._common import _require
from rootcause_core.settings import Settings, get_settings

API_KEY_HEADER = "X-API-Key"
OPENVIKING_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

_clients: dict[Settings, httpx.AsyncClient] = {}


def _build(settings: Settings) -> httpx.AsyncClient:
    base_url = _require(settings.openviking_url, "OPENVIKING_URL")
    headers: dict[str, str] = {}
    if settings.openviking_api_key is not None:
        headers[API_KEY_HEADER] = settings.openviking_api_key.get_secret_value()
    return httpx.AsyncClient(base_url=str(base_url), headers=headers, timeout=OPENVIKING_TIMEOUT)


def openviking_http(settings: Settings | None = None) -> httpx.AsyncClient:
    """Return the shared OpenViking client; the API lifespan closes it via :func:`close_clients`.

    A client found closed is rebuilt (it holds no resources left to release).

    Raises:
        ConfigurationError: ``ROOTCAUSE_OPENVIKING_URL`` is unset.
    """
    resolved = settings if settings is not None else get_settings()
    client = _clients.get(resolved)
    if client is None or client.is_closed:
        client = _build(resolved)
        _clients[resolved] = client
    return client


async def close_clients() -> None:
    """Close every cached HTTP client and forget them (call from the API lifespan, T035)."""
    open_clients = list(_clients.values())
    _clients.clear()
    for client in open_clients:
        await client.aclose()


def reset_http_cache() -> None:
    """Forget cached HTTP clients without closing them (tests only)."""
    _clients.clear()
