"""Alembic migrations against a real, throwaway Postgres with pgvector.

Adapted from the rootcause-core suite: modules moved to ``rootcause_db`` and every
test starts from a bootstrapped database (the migration no longer creates the
extension or schemas).
"""

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
INSERT_CONNECTION = (
    "INSERT INTO app.connections (id, display_name, dialect, host, port, database, "
    "username, encrypted_secret, limits, created_at, updated_at, tenant_id) VALUES "
    "(:id, 'n', 'postgresql', 'h', 5432, 'd', 'u', '\\x00', '{}', now(), now(), 't')"
)


def _sync_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
def engine(bootstrapped_url: str) -> Iterator[sa.Engine]:
    created = sa.create_engine(_sync_url(bootstrapped_url))
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


def _versions(engine: sa.Engine) -> list[str]:
    with engine.connect() as connection:
        return [
            r for (r,) in connection.execute(sa.text("SELECT version_num FROM app.alembic_version"))
        ]


def _insert_connection(engine: sa.Engine) -> uuid.UUID:
    connection_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text(INSERT_CONNECTION), {"id": connection_id})
    return connection_id


def test_upgrade_head_creates_tables_in_a_bootstrapped_database(bootstrapped_url, engine):
    from rootcause_db.migrate import upgrade

    upgrade(bootstrapped_url, "head")

    assert _tables(engine) == EXPECTED_TABLES
    assert len(_versions(engine)) == 1


def test_downgrade_base_leaves_no_tables_or_enum_types(bootstrapped_url, engine):
    from rootcause_db.migrate import downgrade, upgrade

    upgrade(bootstrapped_url, "head")

    downgrade(bootstrapped_url, "base")

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


def test_upgrade_after_downgrade_round_trips(bootstrapped_url, engine):
    from rootcause_db.migrate import downgrade, upgrade

    upgrade(bootstrapped_url, "head")
    downgrade(bootstrapped_url, "base")

    upgrade(bootstrapped_url, "head")

    assert _tables(engine) == EXPECTED_TABLES


def test_upgrade_head_twice_is_idempotent(bootstrapped_url, engine):
    from rootcause_db.migrate import upgrade

    upgrade(bootstrapped_url, "head")
    first = _versions(engine)

    upgrade(bootstrapped_url, "head")

    assert _versions(engine) == first
    assert _tables(engine) == EXPECTED_TABLES


def test_models_match_the_migrated_schema_including_server_defaults(bootstrapped_url, engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from rootcause_db.migrate import upgrade

    import rootcause_catalog.models  # noqa: F401
    import rootcause_connections.models  # noqa: F401
    from rootcause_core.db.base import Base

    upgrade(bootstrapped_url, "head")

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
                "compare_server_default": True,
            },
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == []


def test_connection_and_audit_rows_round_trip(bootstrapped_url, engine):
    from rootcause_db.migrate import upgrade
    from sqlalchemy.orm import Session

    from rootcause_connections.models import Connection, QueryAuditLog, QueryStatus

    upgrade(bootstrapped_url, "head")
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
                status=QueryStatus.OK,
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
        log = session.scalars(sa.select(QueryAuditLog)).one()
        assert log.status is QueryStatus.OK
        assert log.sql == "SELECT 1"
        assert log.error_code is None


def test_connection_id_is_generated_by_the_database(bootstrapped_url, engine):
    from rootcause_db.migrate import upgrade

    upgrade(bootstrapped_url, "head")

    with engine.begin() as connection:
        generated = connection.execute(
            sa.text(
                "INSERT INTO app.connections (display_name, dialect, host, port, database, "
                "username, encrypted_secret) VALUES ('n', 'postgresql', 'h', 5432, 'd', 'u', "
                "'\\x00') RETURNING id"
            )
        ).scalar_one()
    assert isinstance(generated, uuid.UUID)


def test_updated_at_advances_on_orm_update(bootstrapped_url, engine):
    from rootcause_db.migrate import upgrade
    from sqlalchemy.orm import Session

    from rootcause_connections.models import Connection

    upgrade(bootstrapped_url, "head")
    connection_id = _insert_connection(engine)
    with Session(engine) as session:
        stored = session.get(Connection, connection_id)
        assert stored is not None
        before = stored.updated_at

    with Session(engine) as session:
        stored = session.get(Connection, connection_id)
        assert stored is not None
        stored.display_name = "renamed"
        session.commit()

    with Session(engine) as session:
        stored = session.get(Connection, connection_id)
        assert stored is not None
        assert stored.updated_at > before


def test_audit_status_rejects_unknown_values(bootstrapped_url, engine):
    from rootcause_db.migrate import upgrade

    upgrade(bootstrapped_url, "head")
    connection_id = _insert_connection(engine)

    with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO audit.query_log (connection_id, sql, started_at, status) "
                "VALUES (:id, 'SELECT 1', now(), 'bogus')"
            ),
            {"id": connection_id},
        )


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_port_outside_1_to_65535_is_rejected_by_the_database(bootstrapped_url, engine, port):
    from rootcause_db.migrate import upgrade

    upgrade(bootstrapped_url, "head")

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(INSERT_CONNECTION.replace("5432", str(port))), {"id": uuid.uuid4()}
        )


@pytest.mark.parametrize("port", [1, 65535])
def test_port_boundaries_are_accepted(bootstrapped_url, engine, port):
    from rootcause_db.migrate import upgrade

    upgrade(bootstrapped_url, "head")

    with engine.begin() as connection:
        connection.execute(
            sa.text(INSERT_CONNECTION.replace("5432", str(port))), {"id": uuid.uuid4()}
        )
