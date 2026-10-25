"""Every error path returns an Envelope and never leaks internals."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
from pydantic import BaseModel

from rootcause_core.errors import ConfigurationError, RootCauseError

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.testclient import TestClient


class _Payload(BaseModel):
    name: str
    count: int


def _test_router() -> "APIRouter":
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/_t/root")
    def root_error() -> None:
        raise RootCauseError("boom")

    @router.get("/_t/config")
    def config_error() -> None:
        raise ConfigurationError("cfg", variables=("X",))

    @router.get("/_t/crash")
    def crash() -> None:
        raise RuntimeError("secret detail")

    @router.post("/_t/validate")
    def validate(payload: _Payload) -> dict[str, str]:
        return {"name": payload.name}

    return router


@pytest.fixture
def client(make_app: Callable[..., Any]) -> "TestClient":
    from fastapi.testclient import TestClient

    app = make_app()
    app.include_router(_test_router())
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_root_cause_error_returns_400_envelope(client: "TestClient") -> None:
    response = client.get("/_t/root")

    body = response.json()
    assert response.status_code == 400
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "rootcause_error"
    assert body["error"]["message"] == "boom"


@pytest.mark.unit
def test_configuration_error_returns_generic_500(client: "TestClient") -> None:
    response = client.get("/_t/config")

    body = response.json()
    assert response.status_code == 500
    assert body["success"] is False
    assert body["error"]["code"] == "configuration_error"
    assert body["error"]["message"]
    assert "cfg" not in response.text


@pytest.mark.unit
def test_unhandled_exception_returns_500_without_leaking(client: "TestClient") -> None:
    response = client.get("/_t/crash")

    body = response.json()
    assert response.status_code == 500
    assert body["success"] is False
    assert body["error"]["code"] == "internal_error"
    assert "secret detail" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.unit
def test_validation_error_returns_422_with_field_details(client: "TestClient") -> None:
    response = client.post("/_t/validate", json={"name": "x", "count": "not-a-number"})

    body = response.json()
    assert response.status_code == 422
    assert body["success"] is False
    assert body["error"]["code"] == "validation_error"
    assert "count" in str(body["error"]["details"])


@pytest.mark.unit
def test_validation_error_for_missing_body_lists_required_fields(client: "TestClient") -> None:
    response = client.post("/_t/validate", json={})

    details = str(response.json()["error"]["details"])
    assert response.status_code == 422
    assert "name" in details
    assert "count" in details


@pytest.mark.unit
def test_unknown_path_returns_404_envelope(client: "TestClient") -> None:
    response = client.get("/does-not-exist")

    body = response.json()
    assert response.status_code == 404
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["message"]


@pytest.mark.unit
def test_error_responses_carry_request_id_header(client: "TestClient") -> None:
    response = client.get("/_t/crash", headers={"X-Request-ID": "req-42"})

    assert response.headers["X-Request-ID"] == "req-42"
