"""``POST /connections/{id}/test`` and ``GET /connections/{id}/introspect`` without Postgres (RED).

DuckDB connections open the file read-only through the executor lockdown.
Postgres probes against unreachable hosts must fail fast and generically: the
password, the DSN and the driver's message never reach the response or the logs.
"""

import hashlib
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

PATH = "/api/v1/connections"
PASSWORD = "SENTINEL-pw-123"  # gitleaks:allow
PROBE_FIELDS = {"reachable", "read_only", "server_version", "error"}
# Contract: the connect timeout is at most 5 s; generous margin for slow CI.
PROBE_BUDGET_SECONDS = 10.0
NON_ROUTABLE_HOST = "10.255.255.1"


def _create(client: TestClient, body: dict[str, Any]) -> str:
    response = client.post(PATH, json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def _duckdb(client: TestClient, path: Path) -> str:
    return _create(client, {"name": "demo", "kind": "duckdb", "path": str(path)})


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_generic_failure(response: Any, capsys: pytest.CaptureFixture[str]) -> None:
    captured = capsys.readouterr()
    for text in (response.text, captured.out + captured.err):
        assert PASSWORD not in text
        assert "SENTINEL" not in text
        assert "postgresql://" not in text
        assert "postgres://" not in text
        assert "password=" not in text.lower()


# --- DuckDB ------------------------------------------------------------------


def test_duckdb_test_reports_reachable_and_read_only(
    connections_client: TestClient, warehouse_file: Path
) -> None:
    connection_id = _duckdb(connections_client, warehouse_file)
    before = _digest(warehouse_file)

    response = connections_client.post(f"{PATH}/{connection_id}/test")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert set(data) == PROBE_FIELDS
    assert data["reachable"] is True
    assert data["read_only"] is True
    assert isinstance(data["server_version"], str)
    assert data["server_version"]
    assert data["error"] is None
    assert _digest(warehouse_file) == before


def test_duckdb_test_on_deleted_file_is_unreachable_without_leaking_the_path(
    connections_client: TestClient, warehouse_file: Path
) -> None:
    connection_id = _duckdb(connections_client, warehouse_file)
    warehouse_file.unlink()

    response = connections_client.post(f"{PATH}/{connection_id}/test")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reachable"] is False
    assert data["read_only"] is False
    assert data["server_version"] is None
    assert isinstance(data["error"], str)
    assert data["error"]
    assert str(warehouse_file) not in response.text


def test_duckdb_test_on_a_non_duckdb_file_is_unreachable(
    connections_client: TestClient, tmp_path: Path
) -> None:
    garbage = tmp_path / "garbage.duckdb"
    garbage.write_bytes(b"this is not a duckdb database " * 64)
    connection_id = _duckdb(connections_client, garbage)

    response = connections_client.post(f"{PATH}/{connection_id}/test")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reachable"] is False
    assert data["read_only"] is False
    assert data["error"]
    assert str(garbage) not in response.text


def test_duckdb_introspect_returns_schemas_tables_and_columns(
    connections_client: TestClient, warehouse_file: Path
) -> None:
    connection_id = _duckdb(connections_client, warehouse_file)
    before = _digest(warehouse_file)

    response = connections_client.get(f"{PATH}/{connection_id}/introspect")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {
        "schemas": [
            {
                "name": "main",
                "tables": [
                    {
                        "name": "plants",
                        "row_estimate": 3,
                        "columns": [
                            {"name": "plant_id", "type": "VARCHAR", "nullable": False},
                            {"name": "plant_name", "type": "VARCHAR", "nullable": True},
                        ],
                    },
                    {
                        "name": "production",
                        "row_estimate": 5,
                        "columns": [
                            {"name": "ts", "type": "INTEGER", "nullable": True},
                            {"name": "units", "type": "INTEGER", "nullable": False},
                        ],
                    },
                ],
            }
        ]
    }
    assert _digest(warehouse_file) == before


def test_duckdb_introspect_on_deleted_file_returns_503_connection_unavailable(
    connections_client: TestClient, warehouse_file: Path
) -> None:
    connection_id = _duckdb(connections_client, warehouse_file)
    warehouse_file.unlink()

    response = connections_client.get(f"{PATH}/{connection_id}/introspect")

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "connection_unavailable"
    assert str(warehouse_file) not in response.text


# --- unknown ids ---------------------------------------------------------------


@pytest.mark.parametrize(("method", "suffix"), [("POST", "test"), ("GET", "introspect")])
def test_probe_endpoints_return_404_for_unknown_connection(
    connections_client: TestClient, method: str, suffix: str
) -> None:
    response = connections_client.request(method, f"{PATH}/{uuid.uuid4()}/{suffix}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "connection_not_found"


# --- unreachable Postgres -----------------------------------------------------


def test_postgres_test_against_closed_port_is_generic_and_never_leaks_the_password(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection_id = _create(connections_client, postgres_body(host="127.0.0.1", port=1))
    capsys.readouterr()

    response = connections_client.post(f"{PATH}/{connection_id}/test")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert set(data) == PROBE_FIELDS
    assert data["reachable"] is False
    assert data["read_only"] is False
    assert data["server_version"] is None
    assert isinstance(data["error"], str)
    assert data["error"]
    assert "127.0.0.1" not in data["error"]
    _assert_generic_failure(response, capsys)


def test_postgres_test_against_non_routable_host_returns_within_the_connect_budget(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection_id = _create(connections_client, postgres_body(host=NON_ROUTABLE_HOST, port=5432))
    capsys.readouterr()

    started = time.monotonic()
    response = connections_client.post(f"{PATH}/{connection_id}/test")
    elapsed = time.monotonic() - started

    assert elapsed < PROBE_BUDGET_SECONDS
    assert response.status_code == 200
    assert response.json()["data"]["reachable"] is False
    _assert_generic_failure(response, capsys)


def test_postgres_introspect_against_closed_port_returns_503_without_leaks(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection_id = _create(connections_client, postgres_body(host="127.0.0.1", port=1))
    capsys.readouterr()

    response = connections_client.get(f"{PATH}/{connection_id}/introspect")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "connection_unavailable"
    _assert_generic_failure(response, capsys)
