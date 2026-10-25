"""T008: rootcause_core.clients.openviking_http and close_clients (no network)."""

import asyncio
from typing import Any

import pytest

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit

SENTINEL = "sk-SENTINEL-123"
URL = "http://viking.local:1933"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/db",
        "openviking_url": URL,
        "openviking_api_key": SENTINEL,
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.fixture(autouse=True)
def _fresh_clients() -> Any:
    from rootcause_core.clients import close_clients, reset_clients

    reset_clients()
    yield
    asyncio.run(close_clients())
    reset_clients()


def _auth_values(client: Any) -> list[str]:
    return [value for key, value in client.headers.items() if key.lower() != "user-agent"]


def test_base_url_and_auth_header() -> None:
    import httpx
    from rootcause_core.clients import openviking_http

    async def run() -> None:
        client = openviking_http(_settings())

        assert isinstance(client, httpx.AsyncClient)
        assert str(client.base_url).rstrip("/") == URL
        assert any(SENTINEL in value for value in _auth_values(client))
        bearer = client.headers.get("authorization")
        if bearer is not None:
            assert bearer == f"Bearer {SENTINEL}"

    asyncio.run(run())


def test_has_finite_timeout() -> None:
    from rootcause_core.clients import openviking_http

    async def run() -> None:
        timeout = openviking_http(_settings()).timeout
        assert timeout.read is not None
        assert timeout.connect is not None

    asyncio.run(run())


def test_no_auth_header_without_api_key() -> None:
    from rootcause_core.clients import openviking_http

    async def run() -> None:
        client = openviking_http(_settings(openviking_api_key=None))
        assert not any(SENTINEL in value for value in _auth_values(client))
        assert "authorization" not in client.headers

    asyncio.run(run())


def test_client_is_cached_until_reset() -> None:
    from rootcause_core.clients import close_clients, openviking_http, reset_clients

    async def run() -> None:
        settings = _settings()
        first = openviking_http(settings)
        assert openviking_http(settings) is first
        await close_clients()
        reset_clients()
        assert openviking_http(settings) is not first

    asyncio.run(run())


def test_close_clients_closes_the_http_client() -> None:
    from rootcause_core.clients import close_clients, openviking_http

    async def run() -> None:
        client = openviking_http(_settings())
        assert not client.is_closed

        await close_clients()

        assert client.is_closed

    asyncio.run(run())


def test_close_clients_without_clients_is_a_noop() -> None:
    from rootcause_core.clients import close_clients

    asyncio.run(close_clients())


def test_missing_url_names_variable_and_hides_key() -> None:
    from rootcause_core.clients import openviking_http

    async def run() -> None:
        with pytest.raises(ConfigurationError) as caught:
            openviking_http(_settings(openviking_url=None))
        assert "ROOTCAUSE_OPENVIKING_URL" in str(caught.value)
        assert SENTINEL not in str(caught.value)

    asyncio.run(run())
