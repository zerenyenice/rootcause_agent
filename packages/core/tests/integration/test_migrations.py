"""Alembic migrations against a real, throwaway Postgres with pgvector."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

SCHEMAS = ("app", "catalog", "audit")
EXPECTED_TABLES = {
    ("app", "connections"),
    ("audit", "query_log"),
    ("catalog", "versions"),
}
VERSION_TABLE = "alembic_version"


def _sync_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
def engine(fresh_database_url: str) -> Iterator[sa.Engine]:
    created = sa.create_engine(_sync_url(fresh_database_url))
    yield created
    created.dispose()


def _tables(engine: sa.Engine) -> set[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema = ANY(:schemas) AND table_type = 'BASE TABLE'"
            ),
            {"schemas": list(SCHEMAS)},
        )
        return {(schema, name) for schema, name in rows if name != VERSION_TABLE}


def _schemas(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(:s)"
            ),
            {"s": list(SCHEMAS)},
        )
        return {name for (name,) in rows}


def _extensions(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {name for (name,) in connection.execute(sa.text("SELECT extname FROM pg_extension"))}


def test_upgrade_head_creates_schemas_extension_and_tables(fresh_database_url, engine):
    from rootcause_core.db.migrate import upgrade

    upgrade(fresh_database_url, "head")

    assert _schemas(engine) == set(SCHEMAS)
    assert "vector" in _extensions(engine)
    assert _tables(engine) == EXPECTED_TABLES


def test_alembic_version_table_lives_in_the_app_schema(fresh_database_url, engine):
    from rootcause_core.db.migrate import upgrade

    upgrade(fresh_database_url, "head")

    with engine.connect() as connection:
        revisions = connection.execute(sa.text("SELECT version_num FROM app.alembic_version")).all()
    assert len(revisions) == 1


def test_downgrade_base_leaves_no_tables_in_the_schemas(fresh_database_url, engine):
    from rootcause_core.db.migrate import downgrade, upgrade

    upgrade(fresh_database_url, "head")

    downgrade(fresh_database_url, "base")

    assert _tables(engine) == set()
    with engine.connect() as connection:
        leftovers = connection.execute(
            sa.text(
                "SELECT n.nspname, t.typname FROM pg_type t JOIN pg_namespace n "
                "ON n.oid = t.typnamespace WHERE n.nspname = ANY(:s) AND t.typtype = 'e'"
            ),
            {"s": list(SCHEMAS)},
        ).all()
    assert leftovers == [], "downgrade must drop enum types it created"


def test_upgrade_after_downgrade_round_trips(fresh_database_url, engine):
    from rootcause_core.db.migrate import downgrade, upgrade

    upgrade(fresh_database_url, "head")
    downgrade(fresh_database_url, "base")

    upgrade(fresh_database_url, "head")

    assert _tables(engine) == EXPECTED_TABLES


def test_upgrade_head_twice_is_idempotent(fresh_database_url, engine):
    from rootcause_core.db.migrate import upgrade

    upgrade(fresh_database_url, "head")
    with engine.connect() as connection:
        first = connection.execute(sa.text("SELECT version_num FROM app.alembic_version")).all()

    upgrade(fresh_database_url, "head")

    with engine.connect() as connection:
        second = connection.execute(sa.text("SELECT version_num FROM app.alembic_version")).all()
    assert first == second
    assert _tables(engine) == EXPECTED_TABLES


def test_models_match_the_migrated_schema(fresh_database_url, engine):
    import rootcause_catalog.models  # noqa: F401
    import rootcause_connections.models  # noqa: F401
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from rootcause_core.db.base import Base
    from rootcause_core.db.migrate import upgrade

    upgrade(fresh_database_url, "head")

    def include_name(name, type_, parent_names):
        if type_ == "schema":
            return name in SCHEMAS
        return True

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": include_name,
                "version_table_schema": "app",
                "compare_type": True,
            },
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == []


def test_connection_and_audit_rows_round_trip(fresh_database_url, engine):
    from rootcause_connections.models import Connection, QueryAuditLog
    from rootcause_core.db.migrate import upgrade
    from sqlalchemy.orm import Session

    upgrade(fresh_database_url, "head")
    connection_id = uuid.uuid4()
    now = datetime.now(UTC)
    limits = {"timeout_s": 30, "max_rows": 1000, "concurrency": 4, "scan_row_threshold": 5_000_000}

    with Session(engine) as session:
        session.add(
            Connection(
                id=connection_id,
                display_name="Warehouse ☃ '; DROP TABLE x;--",
                dialect="postgresql",
                host="db.example.test",
                port=5432,
                database="warehouse",
                username="readonly",
                encrypted_secret=b"\x00\x01ciphertext",
                limits=limits,
                created_at=now,
                updated_at=now,
                tenant_id="tenant-a",
            )
        )
        session.flush()
        session.add(
            QueryAuditLog(
                connection_id=connection_id,
                thread_id="thread-1",
                agent="analyst",
                purpose="investigate revenue dip",
                sql="SELECT 1",
                started_at=now,
                duration_ms=12,
                row_count=1,
                status="ok",
                request_id="req-1",
                tenant_id="tenant-a",
            )
        )
        session.commit()

    with Session(engine) as session:
        stored = session.get(Connection, connection_id)
        assert stored is not None
        assert stored.display_name == "Warehouse ☃ '; DROP TABLE x;--"
        assert bytes(stored.encrypted_secret) == b"\x00\x01ciphertext"
        assert stored.limits == limits
        assert stored.read_only_verified_at is None
        log = session.scalars(sa.select(QueryAuditLog)).one()
        assert log.id is not None
        assert log.connection_id == connection_id
        assert log.status == "ok"
        assert log.sql == "SELECT 1"
        assert log.error_code is None


def test_audit_status_rejects_unknown_values(fresh_database_url, engine):
    from rootcause_core.db.migrate import upgrade

    upgrade(fresh_database_url, "head")
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

    with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO audit.query_log (connection_id, sql, started_at, status) "
                "VALUES (:id, 'SELECT 1', now(), 'bogus')"
            ),
            {"id": connection_id},
        )
