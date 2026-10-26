"""CORS on the UI API service: the web UI (another origin) must be able to call it."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

ORIGIN = "http://localhost:3000"
EVIL = "http://evil.example"
ALLOW_ORIGIN = "access-control-allow-origin"
ID = "00000000-0000-0000-0000-000000000001"

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/connections", "POST"),
        (f"/api/v1/connections/{ID}", "PATCH"),
        (f"/api/v1/connections/{ID}", "DELETE"),
        (f"/api/v1/connections/{ID}/catalog/tables/{ID}", "PATCH"),
    ],
)
def test_allowed_origin_preflight_succeeds(
    make_client: Callable[..., "TestClient"], path: str, method: str
) -> None:
    client = make_client()

    response = client.options(
        path,
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers[ALLOW_ORIGIN] == ORIGIN
    assert method in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers


def test_preflight_from_disallowed_origin_is_rejected(
    make_client: Callable[..., "TestClient"],
) -> None:
    client = make_client()

    response = client.options(
        "/api/v1/connections",
        headers={"Origin": EVIL, "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 400
    assert ALLOW_ORIGIN not in response.headers


def test_simple_get_from_allowed_origin_carries_allow_origin(
    make_client: Callable[..., "TestClient"],
) -> None:
    client = make_client()

    response = client.get("/health", headers={"Origin": ORIGIN})

    assert response.headers[ALLOW_ORIGIN] == ORIGIN


def test_error_response_carries_allow_origin(make_client: Callable[..., "TestClient"]) -> None:
    client = make_client()

    response = client.get("/api/v1/no-such-route", headers={"Origin": ORIGIN})

    assert response.status_code == 404
    assert response.headers[ALLOW_ORIGIN] == ORIGIN


def test_disallowed_origin_gets_no_allow_origin_header(
    make_client: Callable[..., "TestClient"],
) -> None:
    client = make_client()

    response = client.get("/health", headers={"Origin": EVIL})

    assert ALLOW_ORIGIN not in response.headers


def test_configured_origin_replaces_the_default(
    make_client: Callable[..., "TestClient"],
) -> None:
    client = make_client(api_allowed_origins=json.dumps([EVIL]))

    allowed = client.get("/health", headers={"Origin": EVIL})
    default = client.get("/health", headers={"Origin": ORIGIN})

    assert allowed.headers[ALLOW_ORIGIN] == EVIL
    assert ALLOW_ORIGIN not in default.headers
