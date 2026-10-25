"""``/connections/{id}/test`` and ``/introspect`` against a real Postgres (RED).

Read-only rule (contract): a Postgres connection is ``read_only`` iff its role
is not a superuser AND holds no INSERT, UPDATE, DELETE or TRUNCATE privilege on
any table in a non-system schema AND no CREATE privilege on any non-system
schema or on the database. ``default_transaction_read_only`` is ignored: a
session may override it, so it is no guarantee.

Routes use the in-memory repository (dependency override); only the customer
database is real.
"""

import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PATH = "/api/v1/connections"
UNREACHABLE_APP_DB = "postgresql://u:p@127.0.0.1:1/db"
UNUSED_PASSWORD = "SENTINEL-unused-pw"  # gitleaks:allow


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    from rootcause.api.main import create_app
    from rootcause.api.routes.connections import get_connection_repository
    from rootcause.connections.repository import InMemoryConnectionRepository
    from rootcause.settings import clear_settings_cache

    for name in [key for key in os.environ if key.upper().startswith("ROOTCAUSE_")]:
        if not name.upper().startswith("ROOTCAUSE_TEST_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", UNREACHABLE_APP_DB)
    monkeypatch.setenv("ROOTCAUSE_SECRET_KEY", Fernet.generate_key().decode("ascii"))
    clear_settings_cache()
    app = create_app()
    repository = InMemoryConnectionRepository()
    app.dependency_overrides[get_connection_repository] = lambda: repository
    with TestClient(app) as test_client:
        yield test_client
    clear_settings_cache()


@pytest.fixture
def register(client: TestClient, customer_db: Any) -> Callable[..., str]:
    def _register(user: str, **overrides: Any) -> str:
        is_admin = user == customer_db.admin_user
        password = (customer_db.admin_password or UNUSED_PASSWORD) if is_admin else None
        body = {
            "name": f"customer as {user}",
            "kind": "postgres",
            "host": customer_db.host,
            "port": customer_db.port,
            "database": customer_db.database,
            "user": user,
            "password": password or customer_db.password,
            "sslmode": "disable",
            **overrides,
        }
        response = client.post(PATH, json=body)
        assert response.status_code == 201, response.text
        return str(response.json()["data"]["id"])

    return _register


def _probe(client: TestClient, connection_id: str) -> dict[str, Any]:
    response = client.post(f"{PATH}/{connection_id}/test")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    data: dict[str, Any] = body["data"]
    return data


def test_select_only_role_is_reachable_and_read_only(
    client: TestClient, register: Callable[..., str], customer_db: Any
) -> None:
    data = _probe(client, register(customer_db.reader))

    assert data["reachable"] is True
    assert data["read_only"] is True
    assert re.match(r"^\d+(\.\d+)?", data["server_version"])
    assert data["error"] is None


@pytest.mark.parametrize("role_attr", ["writer", "creator", "guc_writer", "admin_user"])
def test_roles_that_can_write_are_reachable_but_not_read_only(
    client: TestClient, register: Callable[..., str], customer_db: Any, role_attr: str
) -> None:
    data = _probe(client, register(getattr(customer_db, role_attr)))

    assert data["reachable"] is True
    assert data["read_only"] is False
    assert data["error"] is None


def test_unknown_database_is_unreachable_with_a_generic_error(
    client: TestClient,
    register: Callable[..., str],
    customer_db: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection_id = register(customer_db.reader, database="missing_db_for_probe")
    capsys.readouterr()

    response = client.post(f"{PATH}/{connection_id}/test")

    data = response.json()["data"]
    assert data["reachable"] is False
    assert data["read_only"] is False
    assert data["server_version"] is None
    assert data["error"]
    captured = capsys.readouterr()
    for text in (response.text, captured.out + captured.err):
        assert customer_db.password not in text
        assert "postgresql://" not in text
    assert "missing_db_for_probe" not in data["error"]


def test_probe_leaves_the_customer_database_unchanged(
    client: TestClient, register: Callable[..., str], customer_db: Any
) -> None:
    import psycopg

    _probe(client, register(customer_db.writer))

    with psycopg.connect(customer_db.admin_url) as connection:
        row = connection.execute("SELECT count(*) FROM public.orders").fetchone()
    assert row == (300,)


def test_introspect_lists_user_schemas_tables_and_columns(
    client: TestClient, register: Callable[..., str], customer_db: Any
) -> None:
    connection_id = register(customer_db.reader)

    response = client.get(f"{PATH}/{connection_id}/introspect")

    assert response.status_code == 200, response.text
    schemas = {schema["name"]: schema for schema in response.json()["data"]["schemas"]}
    assert set(schemas) == {"public", "sales"}
    tables = {table["name"]: table for table in schemas["public"]["tables"]}
    assert set(tables) == {"big", "orders"}
    assert tables["orders"]["row_estimate"] == 300
    assert tables["orders"]["columns"] == [
        {"name": "id", "type": "integer", "nullable": False},
        {"name": "amount", "type": "numeric", "nullable": False},
        {"name": "status", "type": "text", "nullable": True},
    ]
    regions = schemas["sales"]["tables"]
    assert [table["name"] for table in regions] == ["regions"]
    assert regions[0]["row_estimate"] == 2
