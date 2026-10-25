"""GET /health reports dependency status without ever raising."""

import time
from collections.abc import Callable
from typing import Any

import pytest

HEALTH_BUDGET_SECONDS = 5.0


async def _ok(*_args: Any, **_kwargs: Any) -> str:
    return "ok"


@pytest.mark.unit
def test_health_degraded_when_postgres_down_and_openviking_unset(
    make_client: Callable[..., Any],
) -> None:
    import rootcause_api

    client = make_client()

    response = client.get("/health")

    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"]["postgres"] == "fail"
    assert body["data"]["checks"]["openviking"] == "skipped"
    assert body["data"]["version"] == rootcause_api.__version__


@pytest.mark.unit
def test_health_openviking_fail_when_unreachable(make_client: Callable[..., Any]) -> None:
    client = make_client(openviking_url="http://127.0.0.1:1")

    body = client.get("/health").json()

    assert body["data"]["checks"]["openviking"] == "fail"
    assert body["data"]["status"] == "degraded"


@pytest.mark.unit
def test_health_ok_when_both_probes_succeed(
    make_client: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rootcause_api.routes.health.check_postgres", _ok)
    monkeypatch.setattr("rootcause_api.routes.health.check_openviking", _ok)
    client = make_client(openviking_url="http://127.0.0.1:1")

    body = client.get("/health").json()

    assert body["data"]["status"] == "ok"
    assert body["data"]["checks"] == {"postgres": "ok", "openviking": "ok"}


@pytest.mark.unit
def test_health_ok_when_postgres_ok_and_openviking_skipped(
    make_client: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rootcause_api.routes.health.check_postgres", _ok)
    client = make_client()

    body = client.get("/health").json()

    assert body["data"]["status"] == "ok"
    assert body["data"]["checks"]["openviking"] == "skipped"


@pytest.mark.unit
def test_health_survives_probe_raising(
    make_client: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise OSError("connection refused")

    monkeypatch.setattr("rootcause_api.routes.health.check_postgres", _boom)
    client = make_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["checks"]["postgres"] == "fail"


@pytest.mark.integration
def test_health_completes_within_budget_with_unreachable_dependencies(
    make_client: Callable[..., Any],
) -> None:
    client = make_client(openviking_url="http://127.0.0.1:1")

    started = time.monotonic()
    response = client.get("/health")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < HEALTH_BUDGET_SECONDS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://127.0.0.1:1", "http://127.0.0.1:1/health"),
        ("http://127.0.0.1:1/", "http://127.0.0.1:1/health"),
        ("http://127.0.0.1:1/api", "http://127.0.0.1:1/api/health"),
        ("http://127.0.0.1:1/api/", "http://127.0.0.1:1/api/health"),
    ],
)
def test_openviking_health_url_appends_health_to_base_path(
    make_app: Callable[..., Any], base: str, expected: str
) -> None:
    from rootcause_api.routes.health import openviking_health_url

    app = make_app(openviking_url=base)

    assert openviking_health_url(app.state.settings) == expected


@pytest.mark.unit
def test_openviking_probe_requests_health_under_the_base_path(
    make_app: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import httpx

    from rootcause_api.routes.health import check_openviking

    requested: list[str] = []

    async def _get(_self: httpx.AsyncClient, url: str, **_kwargs: Any) -> httpx.Response:
        requested.append(str(url))
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    app = make_app(openviking_url="http://127.0.0.1:1/api")

    result = asyncio.run(check_openviking(app.state.settings))

    assert result == "ok"
    assert requested == ["http://127.0.0.1:1/api/health"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@h:5432/db",
        "postgresql://u:p@h:5432/db",
        "postgresql+psycopg://u:p@h:5432/db",
        "postgresql+asyncpg://u:p@h:5432/db",
    ],
)
def test_asyncpg_url_normalises_every_postgres_scheme(url: str) -> None:
    from rootcause_api.routes.health import asyncpg_url

    assert asyncpg_url(url) == "postgresql+asyncpg://u:p@h:5432/db"
