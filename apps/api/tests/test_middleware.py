"""Request-id propagation and rate limiting."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest


@pytest.mark.unit
def test_request_id_is_generated_when_absent(make_client: Callable[..., Any]) -> None:
    client = make_client()

    response = client.get("/version")

    assert str(uuid.UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


@pytest.mark.unit
def test_request_id_is_unique_per_request(make_client: Callable[..., Any]) -> None:
    client = make_client()

    first = client.get("/version").headers["X-Request-ID"]
    second = client.get("/version").headers["X-Request-ID"]

    assert first != second


@pytest.mark.unit
def test_request_id_is_echoed_when_provided(make_client: Callable[..., Any]) -> None:
    client = make_client()

    response = client.get("/version", headers={"X-Request-ID": "trace-me-123"})

    assert response.headers["X-Request-ID"] == "trace-me-123"


@pytest.mark.unit
def test_fourth_request_is_rate_limited_with_envelope(make_client: Callable[..., Any]) -> None:
    client = make_client(rate_limit="3/minute")

    statuses = [client.get("/version").status_code for _ in range(3)]
    limited = client.get("/version")

    body = limited.json()
    assert statuses == [200, 200, 200]
    assert limited.status_code == 429
    assert body["success"] is False
    assert body["error"]["code"] == "rate_limited"
    assert "X-Request-ID" in limited.headers


@pytest.mark.unit
def test_default_limit_allows_normal_traffic(make_client: Callable[..., Any]) -> None:
    client = make_client()

    statuses = {client.get("/version").status_code for _ in range(10)}

    assert statuses == {200}
