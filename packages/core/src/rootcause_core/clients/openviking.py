"""OpenViking HTTP client (v0.3.x authenticates with the ``X-API-Key`` header)."""

import httpx

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import ENV_PREFIX, Settings, get_settings

API_KEY_HEADER = "X-API-Key"
OPENVIKING_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

_clients: dict[Settings, httpx.AsyncClient] = {}


def _build(settings: Settings) -> httpx.AsyncClient:
    if settings.openviking_url is None:
        raise ConfigurationError(
            f"{ENV_PREFIX}OPENVIKING_URL is required to reach OpenViking.",
            variables=(f"{ENV_PREFIX}OPENVIKING_URL",),
        )
    headers: dict[str, str] = {}
    if settings.openviking_api_key is not None:
        headers[API_KEY_HEADER] = settings.openviking_api_key.get_secret_value()
    return httpx.AsyncClient(
        base_url=str(settings.openviking_url), headers=headers, timeout=OPENVIKING_TIMEOUT
    )


def openviking_http(settings: Settings | None = None) -> httpx.AsyncClient:
    """Return the shared OpenViking client; call :func:`close_clients` on shutdown.

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
    """Close every cached HTTP client and forget them."""
    open_clients = list(_clients.values())
    _clients.clear()
    for client in open_clients:
        await client.aclose()


def reset_http_cache() -> None:
    """Forget cached HTTP clients without closing them (tests only)."""
    _clients.clear()
