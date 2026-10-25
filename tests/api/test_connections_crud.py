"""Connection CRUD over ``/api/v1/connections`` (RED).

Routes use the in-memory repository through a dependency override, so no
Postgres is needed. Every response is checked for secret leakage: no key named
password/secret/encrypted and never the password value itself.
"""

import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

PATH = "/api/v1/connections"
PASSWORD = "SENTINEL-pw-123"  # gitleaks:allow
NEW_PASSWORD = "SENTINEL-rotated-pw-456"  # gitleaks:allow
PUBLIC_FIELDS = {
    "id",
    "name",
    "kind",
    "host",
    "port",
    "database",
    "user",
    "sslmode",
    "path",
    "created_at",
    "updated_at",
}


def _assert_no_secret(response: Any, forbidden_keys: Callable[[Any], list[str]]) -> None:
    assert PASSWORD not in response.text
    assert NEW_PASSWORD not in response.text
    assert forbidden_keys(response.json()) == []


def _create(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(PATH, json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _stored(repository: Any, connection_id: str) -> Any:
    record = asyncio.run(repository.get(uuid.UUID(connection_id)))
    assert record is not None
    return record


def test_create_postgres_returns_201_envelope_with_public_fields_only(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    forbidden_keys: Callable[[Any], list[str]],
) -> None:
    response = connections_client.post(PATH, json=postgres_body(port=6543, sslmode="require"))

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert set(data) == PUBLIC_FIELDS
    uuid.UUID(data["id"])
    assert data["name"] == "plant warehouse"
    assert data["kind"] == "postgres"
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 6543
    assert data["database"] == "warehouse"
    assert data["user"] == "analyst"
    assert data["sslmode"] == "require"
    assert data["path"] is None
    _assert_no_secret(response, forbidden_keys)


def test_create_postgres_defaults_port_5432_and_sslmode_prefer(
    connections_client: TestClient, postgres_body: Callable[..., dict[str, Any]], drop: object
) -> None:
    data = _create(connections_client, postgres_body(port=drop))

    assert data["port"] == 5432
    assert data["sslmode"] == "prefer"


def test_create_duckdb_returns_path_and_null_network_fields(
    connections_client: TestClient,
    warehouse_file: Path,
    forbidden_keys: Callable[[Any], list[str]],
) -> None:
    response = connections_client.post(
        PATH, json={"name": "demo", "kind": "duckdb", "path": str(warehouse_file)}
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert set(data) == PUBLIC_FIELDS
    assert data["kind"] == "duckdb"
    assert data["path"] == str(warehouse_file)
    for field in ("host", "port", "database", "user", "sslmode"):
        assert data[field] is None, field
    assert forbidden_keys(response.json()) == []


def test_stored_secret_is_encrypted_and_decrypts_with_the_configured_key(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    connection_repository: Any,
    secret_key: str,
) -> None:
    from cryptography.fernet import Fernet

    data = _create(connections_client, postgres_body())

    stored = _stored(connection_repository, data["id"]).encrypted_secret
    assert isinstance(stored, bytes)
    assert PASSWORD.encode() not in stored
    assert Fernet(secret_key.encode()).decrypt(stored).decode() == PASSWORD


def test_duckdb_connection_stores_no_secret(
    connections_client: TestClient, warehouse_file: Path, connection_repository: Any
) -> None:
    data = _create(
        connections_client, {"name": "demo", "kind": "duckdb", "path": str(warehouse_file)}
    )

    assert _stored(connection_repository, data["id"]).encrypted_secret is None


def test_create_never_logs_the_password(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()

    _create(connections_client, postgres_body())

    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err


def test_list_returns_connections_in_creation_order_without_secrets(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    warehouse_file: Path,
    forbidden_keys: Callable[[Any], list[str]],
) -> None:
    first = _create(connections_client, postgres_body(name="first"))
    second = _create(
        connections_client, {"name": "second", "kind": "duckdb", "path": str(warehouse_file)}
    )

    response = connections_client.get(PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert [item["id"] for item in body["data"]] == [first["id"], second["id"]]
    assert body["meta"]["total"] == 2
    _assert_no_secret(response, forbidden_keys)


def test_list_is_empty_initially(connections_client: TestClient) -> None:
    body = connections_client.get(PATH).json()

    assert body["success"] is True
    assert body["data"] == []
    assert body["meta"]["total"] == 0


def test_get_returns_the_connection_without_secrets(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    forbidden_keys: Callable[[Any], list[str]],
) -> None:
    created = _create(connections_client, postgres_body())

    response = connections_client.get(f"{PATH}/{created['id']}")

    assert response.status_code == 200
    assert response.json()["data"] == created
    _assert_no_secret(response, forbidden_keys)


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_unknown_id_returns_404_connection_not_found(
    connections_client: TestClient, method: str
) -> None:
    kwargs: dict[str, Any] = {"json": {"name": "x"}} if method == "patch" else {}

    response = connections_client.request(method.upper(), f"{PATH}/{uuid.uuid4()}", **kwargs)

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "connection_not_found"


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "1", "' OR 1=1 --"])
def test_non_uuid_id_returns_422(connections_client: TestClient, bad_id: str) -> None:
    response = connections_client.get(f"{PATH}/{bad_id}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_patch_updates_mutable_postgres_fields(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    forbidden_keys: Callable[[Any], list[str]],
) -> None:
    created = _create(connections_client, postgres_body())
    changes = {
        "name": "renamed",
        "host": "db.internal",
        "port": 15432,
        "database": "dwh",
        "user": "reader",
        "sslmode": "verify-full",
    }

    response = connections_client.patch(f"{PATH}/{created['id']}", json=changes)

    assert response.status_code == 200
    data = response.json()["data"]
    assert {key: data[key] for key in changes} == changes
    assert data["id"] == created["id"]
    assert data["kind"] == "postgres"
    assert connections_client.get(f"{PATH}/{created['id']}").json()["data"] == data
    _assert_no_secret(response, forbidden_keys)


def test_patch_password_reencrypts_and_is_never_returned(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    connection_repository: Any,
    secret_key: str,
    forbidden_keys: Callable[[Any], list[str]],
) -> None:
    from cryptography.fernet import Fernet

    created = _create(connections_client, postgres_body())
    before = _stored(connection_repository, created["id"]).encrypted_secret

    response = connections_client.patch(f"{PATH}/{created['id']}", json={"password": NEW_PASSWORD})

    assert response.status_code == 200
    _assert_no_secret(response, forbidden_keys)
    after = _stored(connection_repository, created["id"]).encrypted_secret
    assert after != before
    assert NEW_PASSWORD.encode() not in after
    assert Fernet(secret_key.encode()).decrypt(after).decode() == NEW_PASSWORD


def test_patch_duckdb_path_to_another_existing_file(
    connections_client: TestClient, warehouse_file: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other.duckdb"
    other.write_bytes(warehouse_file.read_bytes())
    created = _create(
        connections_client, {"name": "demo", "kind": "duckdb", "path": str(warehouse_file)}
    )

    response = connections_client.patch(f"{PATH}/{created['id']}", json={"path": str(other)})

    assert response.status_code == 200
    assert response.json()["data"]["path"] == str(other)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "duckdb"},
        {"unknown": "x"},
        {"name": None},
        {"port": None},
        {"name": ""},
        {"port": 0},
        {"port": 65536},
        {"sslmode": "allow"},
        {"password": ""},
        {"path": "/tmp/whatever.duckdb"},
    ],
)
def test_patch_postgres_rejects_invalid_or_inapplicable_changes_with_422(
    connections_client: TestClient,
    postgres_body: Callable[..., dict[str, Any]],
    changes: dict[str, Any],
) -> None:
    created = _create(connections_client, postgres_body())

    response = connections_client.patch(f"{PATH}/{created['id']}", json=changes)

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert connections_client.get(f"{PATH}/{created['id']}").json()["data"] == created


@pytest.mark.parametrize(
    "changes",
    [
        {"host": "db.internal"},
        {"password": PASSWORD},
        {"path": "relative.duckdb"},
        {"path": "/definitely/missing/file.duckdb"},
        {"path": None},
    ],
)
def test_patch_duckdb_rejects_invalid_or_inapplicable_changes_with_422(
    connections_client: TestClient, warehouse_file: Path, changes: dict[str, Any]
) -> None:
    created = _create(
        connections_client, {"name": "demo", "kind": "duckdb", "path": str(warehouse_file)}
    )

    response = connections_client.patch(f"{PATH}/{created['id']}", json=changes)

    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert connections_client.get(f"{PATH}/{created['id']}").json()["data"] == created


def test_delete_removes_the_connection(
    connections_client: TestClient, postgres_body: Callable[..., dict[str, Any]]
) -> None:
    created = _create(connections_client, postgres_body())

    response = connections_client.delete(f"{PATH}/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {"id": created["id"], "deleted": True}
    assert connections_client.get(f"{PATH}/{created['id']}").status_code == 404
    assert connections_client.get(PATH).json()["data"] == []


@pytest.mark.parametrize(
    "name",
    [
        "Üretim hattı 🏭",
        "x'; DROP TABLE app.connections; --",
        "<script>alert(1)</script>",
        "a" * 100,
    ],
)
def test_names_with_unicode_and_metacharacters_round_trip_verbatim(
    connections_client: TestClient, postgres_body: Callable[..., dict[str, Any]], name: str
) -> None:
    created = _create(connections_client, postgres_body(name=name))

    fetched = connections_client.get(f"{PATH}/{created['id']}").json()["data"]

    assert fetched["name"] == name


def test_missing_secret_key_returns_configuration_error_and_stores_nothing(
    make_connections_app: Callable[..., Any],
    postgres_body: Callable[..., dict[str, Any]],
    connection_repository: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with TestClient(make_connections_app(), raise_server_exceptions=False) as client:
        capsys.readouterr()
        response = client.post(PATH, json=postgres_body())

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "configuration_error"
    assert PASSWORD not in response.text
    captured = capsys.readouterr()
    logged = captured.out + captured.err
    assert "ROOTCAUSE_SECRET_KEY" in logged
    assert PASSWORD not in logged
    assert asyncio.run(connection_repository.list()) == ()


def test_responses_are_json_envelopes_with_request_id(
    connections_client: TestClient, postgres_body: Callable[..., dict[str, Any]]
) -> None:
    response = connections_client.post(
        PATH, json=postgres_body(), headers={"X-Request-ID": "req-conn-1"}
    )

    assert response.headers["X-Request-ID"] == "req-conn-1"
    assert json.loads(response.text)["meta"]["request_id"] == "req-conn-1"
