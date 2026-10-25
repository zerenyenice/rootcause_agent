"""Tamper probes as the runtime role: the audit log and its guard must be untouchable.

Mirrors the security reviewer's probes (t2.sh, t5.sh): every statement below must
fail with a permission error (42501) or the append-only trigger's exception.
"""

import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

REJECTED_SQLSTATES = {"42501", "P0001"}  # insufficient_privilege, raise_exception
TRIGGER = "query_log_append_only"
FUNCTION = "audit.reject_query_log_mutation"


def _sync(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
def runtime(migrated_roles) -> Iterator[sa.Engine]:
    """Engine connecting as the app role, with one connection and one audit row seeded."""
    migrator = sa.create_engine(_sync(migrated_roles.migration_url))
    with migrator.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO app.connections (id, display_name, dialect, host, port, database, "
                "username, encrypted_secret) VALUES (:id, 'n', 'postgresql', 'h', 5432, 'd', 'u', "
                "'\\x00')"
            ),
            {"id": uuid.uuid4()},
        )
        connection.execute(
            sa.text(
                "INSERT INTO audit.query_log (connection_id, sql, started_at, status) "
                "SELECT id, 'SELECT 1', now(), 'ok' FROM app.connections"
            )
        )
    migrator.dispose()
    engine = sa.create_engine(_sync(migrated_roles.app_url))
    yield engine
    engine.dispose()


def _assert_rejected(engine: sa.Engine, *statements: str) -> None:
    with pytest.raises(sa.exc.DBAPIError) as raised, engine.begin() as connection:
        for statement in statements:
            connection.execute(sa.text(statement))
    assert getattr(raised.value.orig, "sqlstate", None) in REJECTED_SQLSTATES, raised.value


TAMPER_STATEMENTS = {
    "update": "UPDATE audit.query_log SET sql = 'tampered'",
    "delete": "DELETE FROM audit.query_log",
    "truncate": "TRUNCATE audit.query_log",
    "disable-trigger": f"ALTER TABLE audit.query_log DISABLE TRIGGER {TRIGGER}",
    "disable-all-triggers": "ALTER TABLE audit.query_log DISABLE TRIGGER ALL",
    "drop-trigger": f"DROP TRIGGER {TRIGGER} ON audit.query_log",
    "replace-function": (
        f"CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RETURN NEW; END $$"
    ),
    "alter-column-type": "ALTER TABLE audit.query_log ALTER COLUMN sql TYPE text USING 'rewritten'",
    "drop-table": "DROP TABLE audit.query_log",
    "drop-connections": "DROP TABLE app.connections CASCADE",
    "rename-table": "ALTER TABLE audit.query_log RENAME TO query_log_old",
    "create-table-audit": "CREATE TABLE audit.shadow (x int)",
    "create-table-public": "CREATE TABLE public.shadow (x int)",
    "update-alembic-version": "UPDATE app.alembic_version SET version_num = '0001'",
    "delete-alembic-version": "DELETE FROM app.alembic_version",
    "replication-role": "SET session_replication_role = replica",
    "set-role-owner": "SET ROLE {owner}",
}


@pytest.mark.parametrize("name", sorted(TAMPER_STATEMENTS))
def test_runtime_role_cannot_tamper(runtime, migrated_roles, name):
    statement = TAMPER_STATEMENTS[name].format(owner=migrated_roles.owner)

    _assert_rejected(runtime, statement)


def test_tamper_attempts_leave_the_audit_log_and_trigger_intact(runtime, migrated_roles):
    for name, statement in TAMPER_STATEMENTS.items():
        try:
            with runtime.begin() as connection:
                connection.execute(sa.text(statement.format(owner=migrated_roles.owner)))
        except sa.exc.DBAPIError:
            continue
        pytest.fail(f"{name} unexpectedly succeeded for the runtime role")

    owner_view = sa.create_engine(_sync(migrated_roles.admin_url))
    with owner_view.connect() as connection:
        rows = connection.execute(sa.text("SELECT sql FROM audit.query_log")).scalars().all()
        enabled = (
            connection.execute(
                sa.text(
                    "SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'audit.query_log'::regclass "
                    "AND NOT tgisinternal"
                )
            )
            .scalars()
            .all()
        )
    owner_view.dispose()
    assert rows == ["SELECT 1"]
    assert enabled and set(enabled) == {"O"}


def test_runtime_role_can_insert_and_select_audit_rows(runtime):
    with runtime.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO audit.query_log (connection_id, sql, started_at, status) "
                "SELECT id, 'SELECT 2', now(), 'rejected' FROM app.connections"
            )
        )
    with runtime.connect() as connection:
        rows = (
            connection.execute(sa.text("SELECT sql FROM audit.query_log ORDER BY id"))
            .scalars()
            .all()
        )

    assert rows == ["SELECT 1", "SELECT 2"]


def test_runtime_role_can_use_normal_dml_on_connections(runtime):
    connection_id = uuid.uuid4()
    with runtime.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO app.connections (id, display_name, dialect, host, port, database, "
                "username, encrypted_secret) VALUES (:id, 'x', 'postgresql', 'h', 5432, 'd', 'u', "
                "'\\x00')"
            ),
            {"id": connection_id},
        )
        connection.execute(
            sa.text("UPDATE app.connections SET display_name = 'renamed' WHERE id = :id"),
            {"id": connection_id},
        )
    with runtime.begin() as connection:
        name = connection.execute(
            sa.text("SELECT display_name FROM app.connections WHERE id = :id"),
            {"id": connection_id},
        ).scalar_one()
        connection.execute(
            sa.text("DELETE FROM app.connections WHERE id = :id"), {"id": connection_id}
        )

    assert name == "renamed"


def test_runtime_role_cannot_read_the_version_table(runtime):
    _assert_rejected(runtime, "SELECT * FROM app.alembic_version")
