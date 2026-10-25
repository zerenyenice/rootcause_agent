"""Security hardening: error non-disclosure, docs gating, headers, Retry-After."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

from rootcause.errors import ClientError, RootCauseError

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.testclient import TestClient

SECRET_DSN = "dsn postgresql://admin:S3cr3t@db/x"
BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


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


def _router() -> "APIRouter":
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/_t/leak")
    def leak() -> None:
        raise RootCauseError(SECRET_DSN)

    @router.get("/_t/crash")
    def crash() -> None:
        raise RuntimeError("boom")

    return router


def _client(make_app: Callable[..., Any], **overrides: str) -> "TestClient":
    from fastapi.testclient import TestClient

    app = make_app(**overrides)
    app.include_router(_router())
    return TestClient(app, raise_server_exceptions=False)


# --- 1. RootCauseError never echoes its message ---------------------------------


@pytest.mark.unit
def test_root_cause_error_response_and_logs_never_contain_the_message(
    make_app: Callable[..., Any], capsys: pytest.CaptureFixture[str]
) -> None:
    client = _client(make_app)

    response = client.get("/_t/leak")

    output = capsys.readouterr()
    body = response.json()
    assert response.status_code == 500
    assert body["error"]["code"] == "rootcause_error"
    assert body["error"]["message"]
    assert "S3cr3t" not in response.text
    assert "S3cr3t" not in output.out + output.err
    events = [e for e in _json_log_lines(output.out + output.err) if "error_type" in e]
    assert events
    assert any(e["error_type"] == "RootCauseError" for e in events)
    assert all(e.get("request_id") == response.headers["X-Request-ID"] for e in events)


@pytest.mark.unit
def test_base_error_class_attributes() -> None:
    assert RootCauseError.code == "rootcause_error"
    assert RootCauseError.status_code == 500
    assert isinstance(RootCauseError.public_message, str)
    assert RootCauseError.public_message


@pytest.mark.unit
def test_client_error_class_attributes() -> None:
    error = ClientError("x")

    assert error.status_code == 400
    assert error.code == "client_error"
    assert error.public_message == "x"


# --- 2. Docs disabled in prod ---------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_endpoints_are_404_envelopes_in_prod(
    make_client: Callable[..., Any], path: str
) -> None:
    client = make_client(environment="prod")

    response = client.get(path)

    body = response.json()
    assert response.status_code == 404
    assert body["success"] is False
    assert body["data"] is None


@pytest.mark.unit
@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_endpoints_are_available_in_dev(make_client: Callable[..., Any], path: str) -> None:
    client = make_client(environment="dev")

    assert client.get(path).status_code == 200


# --- 4. Security headers --------------------------------------------------------


def _assert_base_headers(response: Any) -> None:
    for name, value in BASE_HEADERS.items():
        assert response.headers.get(name) == value, name


@pytest.mark.unit
def test_security_headers_on_200_404_and_500(make_app: Callable[..., Any]) -> None:
    client = _client(make_app)

    for path, status in (("/version", 200), ("/nope", 404), ("/_t/crash", 500)):
        response = client.get(path)
        assert response.status_code == status
        _assert_base_headers(response)


@pytest.mark.unit
def test_security_headers_on_429(make_client: Callable[..., Any]) -> None:
    client = make_client(rate_limit="1/minute")
    client.get("/version")

    response = client.get("/version")

    assert response.status_code == 429
    _assert_base_headers(response)


@pytest.mark.unit
def test_hsts_absent_outside_prod(make_app: Callable[..., Any]) -> None:
    client = _client(make_app, environment="dev")

    for path in ("/version", "/nope", "/_t/crash"):
        assert "Strict-Transport-Security" not in client.get(path).headers


@pytest.mark.unit
def test_hsts_present_in_prod_on_every_response(make_app: Callable[..., Any]) -> None:
    client = _client(make_app, environment="prod")

    for path in ("/version", "/nope", "/_t/crash"):
        response = client.get(path)
        assert response.headers.get("Strict-Transport-Security"), path
        _assert_base_headers(response)


@pytest.mark.unit
def test_hsts_present_in_prod_on_429(make_client: Callable[..., Any]) -> None:
    client = make_client(environment="prod", rate_limit="1/minute")
    client.get("/version")

    response = client.get("/version")

    assert response.status_code == 429
    assert response.headers.get("Strict-Transport-Security")


# --- 5. Retry-After -------------------------------------------------------------


@pytest.mark.unit
def test_retry_after_is_integer_within_window(make_client: Callable[..., Any]) -> None:
    client = make_client(rate_limit="3/minute")
    for _ in range(3):
        client.get("/version")

    response = client.get("/version")

    raw = response.headers["Retry-After"]
    assert raw.isascii()
    assert raw.isdigit()
    assert 1 <= int(raw) <= 60


@pytest.mark.unit
def test_retry_after_is_one_for_per_second_limit(make_client: Callable[..., Any]) -> None:
    client = make_client(rate_limit="2/second")
    responses = [client.get("/version") for _ in range(3)]

    limited = [r for r in responses if r.status_code == 429]

    assert limited
    assert limited[0].headers["Retry-After"] == "1"
