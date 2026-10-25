"""audit.query_log is append-only (PROJECT.md section 7): UPDATE and DELETE are rejected."""

import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(bootstrapped_url: str) -> Iterator[sa.Engine]:
    from rootcause_db.migrate import upgrade

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
    from rootcause_db.migrate import downgrade

    downgrade(bootstrapped_url, "base")

    with engine.connect() as connection:
        tables = connection.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_schema = 'audit'")
        ).all()
    assert tables == []
