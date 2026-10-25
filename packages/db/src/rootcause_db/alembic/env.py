"""Alembic environment: one database, schemas app/catalog/audit, version table in app.

The extension and schemas are created by ``rootcause-db bootstrap`` (superuser),
not here: migrations run as the unprivileged application role and fail fast
with a clear message when the database has not been bootstrapped. Concurrent
runs are serialised by a transaction-scoped advisory lock.
"""

from collections.abc import MutableMapping

from alembic import context
from sqlalchemy import Connection, func, select, text
from sqlalchemy.engine import Engine

import rootcause_catalog.models  # noqa: F401  (registers catalog and connections tables)
from rootcause_core.db.base import Base
from rootcause_core.db.schemas import ALL_SCHEMAS, APP_SCHEMA
from rootcause_core.settings import get_settings
from rootcause_db.bootstrap import VECTOR_EXTENSION
from rootcause_db.errors import BootstrapRequiredError
from rootcause_db.migrate import URL_ATTRIBUTE, make_migration_engine

# Arbitrary, fixed key shared by every migration run ("rootcaus" in ASCII).
MIGRATION_LOCK_KEY = 0x726F6F7463617573
BOOTSTRAP_HINT = (
    "Run `rootcause-db bootstrap --admin-url <superuser URL>` once for this database, then retry."
)

config = context.config
target_metadata = Base.metadata


def _include_name(
    name: str | None, type_: str, parent_names: MutableMapping[str, str | None]
) -> bool:
    if type_ == "schema":
        return name in ALL_SCHEMAS
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=_include_name,  # type: ignore[arg-type]
        version_table_schema=APP_SCHEMA,
        compare_type=True,
        compare_server_default=True,
    )


def _engine() -> Engine:
    explicit = config.attributes.get(URL_ATTRIBUTE)
    if explicit is not None:
        return make_migration_engine(explicit)
    return make_migration_engine(get_settings().database_url.get_secret_value())


def _missing_prerequisites(connection: Connection) -> list[str]:
    """Bootstrap prerequisites the current role cannot see or use."""
    missing: list[str] = []
    has_extension = connection.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = :name"), {"name": VECTOR_EXTENSION}
    ).first()
    if has_extension is None:
        missing.append(f"extension '{VECTOR_EXTENSION}'")
    rows = connection.execute(
        text(
            "SELECT s.name, n.oid IS NOT NULL, "
            "n.oid IS NOT NULL AND has_schema_privilege(n.oid, 'USAGE') "
            "AND has_schema_privilege(n.oid, 'CREATE') "
            "FROM unnest(CAST(:schemas AS text[])) AS s(name) "
            "LEFT JOIN pg_namespace n ON n.nspname = s.name ORDER BY s.name"
        ),
        {"schemas": list(ALL_SCHEMAS)},
    )
    for schema, exists, usable in rows:
        if not exists:
            missing.append(f"schema '{schema}'")
        elif not usable:
            missing.append(f"USAGE and CREATE on schema '{schema}' for the migrating role")
    return missing


def _require_bootstrap(connection: Connection) -> None:
    missing = _missing_prerequisites(connection)
    if missing:
        raise BootstrapRequiredError(
            f"The database is not bootstrapped: missing {', '.join(missing)}. {BOOTSTRAP_HINT}"
        )


def run_migrations_offline() -> None:
    raise RuntimeError("Offline migrations are not supported: schemas and extensions are required.")


def run_migrations_online() -> None:
    engine = _engine()
    try:
        with engine.connect() as connection:
            _configure(connection)
            with context.begin_transaction():
                # Held until commit: a concurrent run waits here, then sees the
                # committed version table and has nothing left to do.
                connection.execute(select(func.pg_advisory_xact_lock(MIGRATION_LOCK_KEY)))
                _require_bootstrap(connection)
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
