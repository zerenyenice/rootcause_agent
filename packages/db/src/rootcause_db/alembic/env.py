"""Alembic environment: one database, schemas app/catalog/audit, version table in app.

The extension, schemas and grants are created by ``rootcause-db bootstrap``
(superuser), not here. Migrations connect as the migrator role and run under
``SET LOCAL ROLE <db_owner_role>`` so every object is owned by the NOLOGIN
owner role, never by a login role (docs/adr/0006). They fail fast with a clear
message when the database has not been bootstrapped, and in prod they refuse
to run as a superuser. Concurrent runs are serialised by a transaction-scoped
advisory lock.
"""

from collections.abc import MutableMapping

from alembic import context
from sqlalchemy import Connection, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

import rootcause_catalog.models  # noqa: F401  (registers catalog and connections tables)
from rootcause_core.db.base import Base
from rootcause_core.db.schemas import ALL_SCHEMAS, APP_SCHEMA
from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Environment, Settings, get_settings
from rootcause_db.bootstrap import VECTOR_EXTENSION
from rootcause_db.errors import BootstrapRequiredError, DatabaseConnectionError, ProdRefusalError
from rootcause_db.migrate import URL_ATTRIBUTE, make_migration_engine
from rootcause_db.names import ADMIN_URL_VARIABLE, MIGRATION_URL_VARIABLE

# Arbitrary, fixed key shared by every migration run ("rootcaus" in ASCII).
MIGRATION_LOCK_KEY = 0x726F6F7463617573
BOOTSTRAP_HINT = (
    f"Run `rootcause-db bootstrap` once for this database (superuser URL in "
    f"{ADMIN_URL_VARIABLE}), then retry."
)
CONNECTION_FAILED = (
    "could not connect to the database for migrations. Check that the server is reachable "
    f"and that {MIGRATION_URL_VARIABLE} is correct; details are omitted because they can "
    "contain the host and user."
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


def _engine(settings: Settings) -> Engine:
    explicit = config.attributes.get(URL_ATTRIBUTE)
    if explicit is not None:
        return make_migration_engine(explicit)
    if settings.migration_database_url is None:
        raise ConfigurationError(
            f"{MIGRATION_URL_VARIABLE} is not set; migrations connect as the migrator role.",
            variables=(MIGRATION_URL_VARIABLE,),
        )
    return make_migration_engine(settings.migration_database_url.get_secret_value())


def _connect(engine: Engine) -> Connection:
    try:
        return engine.connect()
    except OperationalError:
        # The driver message names the host and user; drop it from the chain.
        raise DatabaseConnectionError(CONNECTION_FAILED) from None


def _refuse_superuser_in_prod(connection: Connection, settings: Settings) -> None:
    if settings.environment is not Environment.PROD:
        return
    is_superuser = connection.execute(
        text("SELECT rolsuper FROM pg_roles WHERE rolname = session_user")
    ).scalar_one()
    if is_superuser:
        raise ProdRefusalError(
            "Migrations refuse to run as a superuser in the prod environment; connect as the "
            f"migrator role via {MIGRATION_URL_VARIABLE}."
        )


def _assume_owner_role(connection: Connection, owner_role: str) -> None:
    """``SET LOCAL ROLE`` to the owner role so new objects belong to it."""
    can_set = connection.execute(
        text("SELECT pg_has_role(session_user, oid, 'MEMBER') FROM pg_roles WHERE rolname = :r"),
        {"r": owner_role},
    ).scalar_one_or_none()
    if can_set is None:
        raise BootstrapRequiredError(
            f"The owner role {owner_role!r} does not exist. {BOOTSTRAP_HINT}"
        )
    if not can_set:
        raise BootstrapRequiredError(
            f"The migrating role is not a member of the owner role {owner_role!r}. {BOOTSTRAP_HINT}"
        )
    quoted = connection.dialect.identifier_preparer.quote_identifier(owner_role)
    # Quoted identifier; exec_driver_sql keeps ':' and '%' literal.
    connection.exec_driver_sql(f"SET LOCAL ROLE {quoted}")


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
    settings = get_settings()
    engine = _engine(settings)
    try:
        with _connect(engine) as connection:
            _configure(connection)
            with context.begin_transaction():
                # Held until commit: a concurrent run waits here, then sees the
                # committed version table and has nothing left to do.
                connection.execute(select(func.pg_advisory_xact_lock(MIGRATION_LOCK_KEY)))
                _refuse_superuser_in_prod(connection, settings)
                _assume_owner_role(connection, settings.db_owner_role)
                _require_bootstrap(connection)
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
