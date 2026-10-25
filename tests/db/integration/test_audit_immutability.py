"""audit.query_log is append-only (PROJECT.md section 7): UPDATE and DELETE are rejected."""

import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(bootstrapped_url: str) -> Iterator[sa.Engine]:
    from rootcause.db.migrate import upgrade

    upgrade(bootstrapped_url, "head")
    created = sa.create_engine(
        bootstrapped_url.replace("postgresql://", "postgresql+psycopg://", 1)
    )
    yield created
    created.dispose()


@pytest.fixture
def log_id(engine: sa.Engine) -> int:
    connection_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO app.connections (id, display_name, dialect, host, port, database, "
                "username, encrypted_secret, limits, created_at, updated_at, tenant_id) VALUES "
                "(:id, 'n', 'postgresql', 'h', 5432, 'd', 'u', '\\x00', '{}', now(), now(), 't')"
            ),
            {"id": connection_id},
        )
        return int(
            connection.execute(
                sa.text(
                    "INSERT INTO audit.query_log (connection_id, sql, started_at, status) "
                    "VALUES (:id, 'SELECT 1', now(), 'ok') RETURNING id"
                ),
                {"id": connection_id},
            ).scalar_one()
        )


def test_insert_still_works(engine, log_id):
    with engine.connect() as connection:
        count = connection.execute(sa.text("SELECT count(*) FROM audit.query_log")).scalar_one()

    assert count == 1


def test_update_is_rejected_and_row_is_unchanged(engine, log_id):
    with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE audit.query_log SET sql = 'tampered' WHERE id = :id"), {"id": log_id}
        )

    with engine.connect() as connection:
        stored = connection.execute(sa.text("SELECT sql FROM audit.query_log")).scalar_one()
    assert stored == "SELECT 1"


def test_delete_is_rejected_and_row_survives(engine, log_id):
    with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM audit.query_log WHERE id = :id"), {"id": log_id})

    with engine.connect() as connection:
        count = connection.execute(sa.text("SELECT count(*) FROM audit.query_log")).scalar_one()
    assert count == 1


def test_downgrade_to_base_is_possible_outside_prod(bootstrapped_url, engine, log_id):
    from rootcause.db.migrate import downgrade

    downgrade(bootstrapped_url, "base")

    with engine.connect() as connection:
        tables = connection.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_schema = 'audit'")
        ).all()
    assert tables == []


def _expect_trigger_rejection(engine: sa.Engine, *statements: str) -> None:
    with pytest.raises(sa.exc.DBAPIError) as raised, engine.begin() as connection:
        for statement in statements:
            connection.execute(sa.text(statement))
    assert "append-only" in str(raised.value.orig)


def test_migrator_truncate_is_rejected_by_the_truncate_trigger(engine, log_id):
    _expect_trigger_rejection(engine, "TRUNCATE audit.query_log")

    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT count(*) FROM audit.query_log")).scalar_one() == 1


def test_owner_role_truncate_is_rejected_by_the_truncate_trigger(engine, log_id, db_roles):
    _expect_trigger_rejection(
        engine, f"SET LOCAL ROLE {db_roles.owner}", "TRUNCATE audit.query_log"
    )


def test_owner_role_truncate_cascade_from_connections_is_rejected(engine, log_id, db_roles):
    _expect_trigger_rejection(
        engine, f"SET LOCAL ROLE {db_roles.owner}", "TRUNCATE app.connections CASCADE"
    )


def test_guard_triggers_exist_enabled_and_cover_each_operation(engine, log_id):
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT tgenabled, tgtype FROM pg_trigger "
                "WHERE tgrelid = 'audit.query_log'::regclass AND NOT tgisinternal"
            )
        ).all()

    assert rows, "autogenerate cannot see triggers, so assert them directly"
    assert {enabled for enabled, _ in rows} == {"O"}
    row_level = 1
    delete, update, truncate = 8, 16, 32
    types = [tgtype for _, tgtype in rows]
    assert any(t & row_level and t & delete and t & update for t in types)
    assert any(not t & row_level and t & truncate for t in types)
