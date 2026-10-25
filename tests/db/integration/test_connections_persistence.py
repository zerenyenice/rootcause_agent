"""Connections persist in ``app.connections`` through the default repository (RED).

The app connects as the runtime role (``ROOTCAUSE_DATABASE_URL`` from
``migrated_roles``). The only column name assumed is ``encrypted_secret``, to
prove encryption at rest; everything else goes through the HTTP API.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PATH = "/api/v1/connections"
PASSWORD = "SENTINEL-pw-123"  # gitleaks:allow
NEW_PASSWORD = "SENTINEL-rotated-pw-456"  # gitleaks:allow


def _sync(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
def secret_key(monkeypatch: pytest.MonkeyPatch, migrated_roles: Any, tmp_path: Path) -> str:
    """Env as a deployment sets it: runtime-role database URL plus a Fernet key."""
    from rootcause.settings import clear_settings_cache

    for name in ("ROOTCAUSE_MIGRATION_DATABASE_URL", "ROOTCAUSE_ADMIN_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert os.environ["ROOTCAUSE_DATABASE_URL"] == migrated_roles.app_url
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("ROOTCAUSE_SECRET_KEY", key)
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()
    return key


@pytest.fixture
def open_client(secret_key: str) -> Iterator[Callable[[], TestClient]]:
    """Factory of TestClients, each over a freshly built app (no in-process state shared)."""
    from rootcause.api.main import create_app
    from rootcause.settings import clear_settings_cache

    clients: list[TestClient] = []

    def _open() -> TestClient:
        clear_settings_cache()
        client = TestClient(create_app())
        client.__enter__()
        clients.append(client)
        return client

    yield _open
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def stored_secret(migrated_roles: Any) -> Iterator[Callable[[str], bytes | None]]:
    engine = sa.create_engine(_sync(migrated_roles.migration_url))

    def _read(connection_id: str) -> bytes | None:
        with engine.connect() as connection:
            row = connection.execute(
                sa.text("SELECT encrypted_secret FROM app.connections WHERE id = :id"),
                {"id": uuid.UUID(connection_id)},
            ).one()
        return None if row[0] is None else bytes(row[0])

    yield _read
    engine.dispose()


def _postgres_body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "plant warehouse",
        "kind": "postgres",
        "host": "db.customer.internal",
        "port": 5433,
        "database": "warehouse",
        "user": "analyst",
        "password": PASSWORD,
        "sslmode": "require",
        **overrides,
    }


def _create(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(PATH, json=body)
    assert response.status_code == 201, response.text
    assert PASSWORD not in response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def test_created_connection_survives_a_new_app_instance(
    open_client: Callable[[], TestClient],
) -> None:
    created = _create(open_client(), _postgres_body())

    fetched = open_client().get(f"{PATH}/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json()["data"] == created


def test_password_is_encrypted_at_rest_and_decrypts_with_the_key(
    open_client: Callable[[], TestClient],
    stored_secret: Callable[[str], bytes | None],
    secret_key: str,
) -> None:
    created = _create(open_client(), _postgres_body())

    stored = stored_secret(created["id"])

    assert stored is not None
    assert PASSWORD.encode() not in stored
    assert Fernet(secret_key.encode()).decrypt(stored).decode() == PASSWORD


def test_patch_persists_changes_and_reencrypts_a_new_password(
    open_client: Callable[[], TestClient],
    stored_secret: Callable[[str], bytes | None],
    secret_key: str,
) -> None:
    client = open_client()
    created = _create(client, _postgres_body())

    response = client.patch(
        f"{PATH}/{created['id']}", json={"name": "renamed", "password": NEW_PASSWORD}
    )

    assert response.status_code == 200
    assert NEW_PASSWORD not in response.text
    assert open_client().get(f"{PATH}/{created['id']}").json()["data"]["name"] == "renamed"
    stored = stored_secret(created["id"])
    assert stored is not None
    assert Fernet(secret_key.encode()).decrypt(stored).decode() == NEW_PASSWORD


def test_duckdb_connection_persists_with_path_and_without_secret(
    open_client: Callable[[], TestClient],
    stored_secret: Callable[[str], bytes | None],
    tmp_path: Path,
) -> None:
    path = tmp_path / "w.duckdb"
    duckdb.connect(str(path)).close()

    created = _create(open_client(), {"name": "demo", "kind": "duckdb", "path": str(path)})

    fetched = open_client().get(f"{PATH}/{created['id']}").json()["data"]
    assert fetched["path"] == str(path)
    assert fetched["host"] is None
    assert fetched["port"] is None
    assert stored_secret(created["id"]) is None


def test_list_and_delete_round_trip_through_the_database(
    open_client: Callable[[], TestClient],
) -> None:
    client = open_client()
    first = _create(client, _postgres_body(name="first"))
    second = _create(client, _postgres_body(name="x'; DROP TABLE app.connections; -- 🏭"))

    listed = open_client().get(PATH).json()["data"]
    assert [item["id"] for item in listed] == [first["id"], second["id"]]
    assert listed[1]["name"] == "x'; DROP TABLE app.connections; -- 🏭"

    assert client.delete(f"{PATH}/{first['id']}").status_code == 200
    assert open_client().get(f"{PATH}/{first['id']}").status_code == 404
    assert [item["id"] for item in open_client().get(PATH).json()["data"]] == [second["id"]]
