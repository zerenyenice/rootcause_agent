"""Read-only verification of Postgres roles that hold no table write grant (RED).

Review finding HIGH 1: a role is NOT read-only when it (or a role it is a member
of) has CREATEDB, CREATEROLE or REPLICATION; is a member of pg_write_server_files,
pg_execute_server_program or pg_read_server_files (reading server files is out of
scope for a role that queries the warehouse); owns a non-system table (even one
whose own write grants were revoked), a materialized view or a schema; or may
CREATE in any database on the same server. Neither ``/test`` nor ``is_read_only()``
may call such a role read-only, and ``/test`` must not record ``read_only_verified_at`` for it.

TEMP on the database is tolerated: PUBLIC holds it by default, temp objects are
session-local, and the executor's READ ONLY transactions refuse CREATE TEMP TABLE.
A role with TEMP (granted directly or through PUBLIC) is still read-only.

Known gap (no test): EXECUTE on SECURITY DEFINER functions that write is not
detected.
"""

from typing import Any

import pytest

from tests.db.integration.conftest import (
    CONNECTIONS_PATH,
    RISKY_ROLE_KINDS,
    ConnectionsApi,
    CustomerDb,
    RiskyRoles,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def api(make_connections_api: Any) -> ConnectionsApi:
    api: ConnectionsApi = make_connections_api()
    return api


def _probe(api: ConnectionsApi, connection_id: str) -> dict[str, Any]:
    response = api.client.post(f"{CONNECTIONS_PATH}/{connection_id}/test")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def test_select_only_reader_with_public_default_temp_is_read_only_and_gets_verified(
    api: ConnectionsApi, customer_db: CustomerDb
) -> None:
    connection_id = api.register(customer_db.reader)

    data = _probe(api, connection_id)

    assert data["reachable"] is True
    assert data["read_only"] is True
    assert api.verified_at(connection_id) is not None


@pytest.mark.parametrize("kind", RISKY_ROLE_KINDS)
def test_probe_reports_risky_role_as_not_read_only_and_does_not_verify_it(
    api: ConnectionsApi, risky_roles: RiskyRoles, kind: str
) -> None:
    connection_id = api.register(risky_roles.roles[kind])

    data = _probe(api, connection_id)

    assert data["reachable"] is True
    assert data["error"] is None
    assert data["read_only"] is False, f"{kind} role was reported read-only"
    assert api.verified_at(connection_id) is None


@pytest.mark.parametrize("kind", RISKY_ROLE_KINDS)
def test_executor_is_read_only_is_false_for_risky_role(
    customer_db: CustomerDb, risky_roles: RiskyRoles, kind: str
) -> None:
    from rootcause.connections.postgres_executor import PostgresExecutor

    with PostgresExecutor(**customer_db.params(risky_roles.roles[kind])) as executor:
        assert executor.is_read_only() is False, f"{kind} role was reported read-only"


def test_executor_is_read_only_is_true_for_reader(customer_db: CustomerDb) -> None:
    from rootcause.connections.postgres_executor import PostgresExecutor

    with PostgresExecutor(**customer_db.params()) as executor:
        assert executor.is_read_only() is True


def test_role_with_temp_on_the_database_is_read_only_and_gets_verified(
    api: ConnectionsApi, risky_roles: RiskyRoles
) -> None:
    connection_id = api.register(risky_roles.roles["temp_is_allowed"])

    data = _probe(api, connection_id)

    assert data["reachable"] is True
    assert data["error"] is None
    assert data["read_only"] is True
    assert api.verified_at(connection_id) is not None


def test_executor_is_read_only_is_true_for_role_with_temp(
    customer_db: CustomerDb, risky_roles: RiskyRoles
) -> None:
    from rootcause.connections.postgres_executor import PostgresExecutor

    params = customer_db.params(risky_roles.roles["temp_is_allowed"])
    with PostgresExecutor(**params) as executor:
        assert executor.is_read_only() is True
