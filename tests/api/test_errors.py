"""Every error path returns an Envelope and never leaks internals."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
from pydantic import BaseModel

from rootcause.errors import ClientError, ConfigurationError, RootCauseError

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.testclient import TestClient


def _json_log_lines(output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


class _Payload(BaseModel):
    name: str
    count: int


def _test_router() -> "APIRouter":
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/_t/client")
    def client_error() -> None:
        raise ClientError("bad input")

    @router.get("/_t/root")
    def root_error() -> None:
        raise RootCauseError("internal thing")

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
def test_client_error_returns_400_envelope_with_its_message(client: "TestClient") -> None:
    response = client.get("/_t/client")

    body = response.json()
    assert response.status_code == 400
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "client_error"
    assert body["error"]["message"] == "bad input"


@pytest.mark.unit
def test_client_error_is_a_root_cause_error() -> None:
    assert issubclass(ClientError, RootCauseError)


@pytest.mark.unit
def test_plain_root_cause_error_returns_generic_500_without_leaking(
    client: "TestClient",
) -> None:
    response = client.get("/_t/root")

    body = response.json()
    assert response.status_code == 500
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "rootcause_error"
    assert body["error"]["message"]
    assert "internal thing" not in response.text


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


@pytest.mark.unit
def test_internal_error_log_carries_request_id(
    capsys: pytest.CaptureFixture[str], make_app: Callable[..., Any]
) -> None:
    from fastapi.testclient import TestClient

    app = make_app()
    app.include_router(_test_router())
    client = TestClient(app, raise_server_exceptions=False)
    capsys.readouterr()
    response = client.get("/_t/crash")

    request_id = response.headers["X-Request-ID"]
    captured = capsys.readouterr()
    events = [
        event
        for event in _json_log_lines(captured.out + captured.err)
        if event.get("event") == "internal_error"
    ]
    assert events
    assert all(event.get("request_id") == request_id for event in events)
