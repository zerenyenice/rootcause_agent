"""Create-body validation for ``POST /api/v1/connections`` (RED).

Invalid bodies answer 422 ``validation_error`` and nothing is stored; the error
body never echoes the submitted password.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

PATH = "/api/v1/connections"
PASSWORD = "SENTINEL-pw-123"  # gitleaks:allow
SSL_MODES = ("disable", "prefer", "require", "verify-full")


def _assert_rejected(response: Any, repository: Any) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "validation_error"
    assert PASSWORD not in response.text
    assert asyncio.run(repository.list()) == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "a" * 101},
        {"name": None},
        {"kind": "mysql"},
        {"kind": None},
        {"port": 0},
        {"port": 65536},
        {"port": -1},
        {"port": "not-a-port"},
        {"sslmode": "allow"},
        {"sslmode": "verify-ca"},
        {"host": ""},
        {"database": ""},
        {"user": ""},
        {"password": ""},
        {"path": "/tmp/x.duckdb"},
        {"encrypted_secret": "x"},
        {"id": "00000000-0000-0000-0000-000000000000"},
    ],
)
def test_invalid_postgres_body_is_rejected(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    connection_repository: Any,
    overrides: dict[str, Any],
) -> None:
    response = connections_client.post(PATH, json=postgres_body(**overrides))

    _assert_rejected(response, connection_repository)


@pytest.mark.parametrize("missing", ["name", "kind", "host", "database", "user", "password"])
def test_postgres_body_missing_required_field_is_rejected(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    connection_repository: Any,
    drop: object,
    missing: str,
) -> None:
    response = connections_client.post(PATH, json=postgres_body(**{missing: drop}))

    _assert_rejected(response, connection_repository)


@pytest.mark.parametrize("port", [1, 65535])
def test_port_boundaries_are_accepted(
    connections_client: TestClient, postgres_body: Callable[..., dict[str, Any]], port: int
) -> None:
    response = connections_client.post(PATH, json=postgres_body(port=port))

    assert response.status_code == 201
    assert response.json()["data"]["port"] == port


@pytest.mark.parametrize("sslmode", SSL_MODES)
def test_every_documented_sslmode_is_accepted(
    connections_client: TestClient, postgres_body: Callable[..., dict[str, Any]], sslmode: str
) -> None:
    response = connections_client.post(PATH, json=postgres_body(sslmode=sslmode))

    assert response.status_code == 201
    assert response.json()["data"]["sslmode"] == sslmode


def test_name_of_exactly_100_characters_is_accepted(
    connections_client: TestClient, postgres_body: Callable[..., dict[str, Any]]
) -> None:
    response = connections_client.post(PATH, json=postgres_body(name="n" * 100))

    assert response.status_code == 201


@pytest.mark.parametrize("body", [None, [], "text", 42])
def test_non_object_body_is_rejected(
    connections_client: TestClient, connection_repository: Any, body: Any
) -> None:
    response = connections_client.post(PATH, json=body)

    _assert_rejected(response, connection_repository)


def _duckdb_cases(tmp_path: Path, warehouse_file: Path) -> dict[str, dict[str, Any]]:
    directory = tmp_path / "dir.duckdb"
    directory.mkdir()
    wrong_suffix = tmp_path / "warehouse.db"
    wrong_suffix.write_bytes(warehouse_file.read_bytes())
    return {
        "relative": {"path": "warehouse.duckdb"},
        "missing": {"path": str(tmp_path / "missing.duckdb")},
        "directory": {"path": str(directory)},
        "wrong_suffix": {"path": str(wrong_suffix)},
        "nul_byte": {"path": str(tmp_path / "x\x00.duckdb")},
        "empty": {"path": ""},
        "absent": {},
        "null": {"path": None},
        "host_given": {"path": str(warehouse_file), "host": "127.0.0.1"},
        "password_given": {"path": str(warehouse_file), "password": PASSWORD},
        "port_given": {"path": str(warehouse_file), "port": 5432},
    }


@pytest.mark.parametrize(
    "case",
    [
        "relative",
        "missing",
        "directory",
        "wrong_suffix",
        "nul_byte",
        "empty",
        "absent",
        "null",
        "host_given",
        "password_given",
        "port_given",
    ],
)
def test_invalid_duckdb_body_is_rejected(
    connections_client: TestClient,
    connection_repository: Any,
    warehouse_file: Path,
    tmp_path: Path,
    case: str,
) -> None:
    extra = _duckdb_cases(tmp_path, warehouse_file)[case]

    response = connections_client.post(PATH, json={"name": "demo", "kind": "duckdb", **extra})

    _assert_rejected(response, connection_repository)
    assert str(tmp_path) not in response.text
