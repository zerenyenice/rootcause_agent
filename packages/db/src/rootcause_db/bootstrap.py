"""Superuser-only database preparation: extension, schemas and app-role grants.

Run once per database (idempotent) with a superuser URL, before migrations run
as the unprivileged application role.
"""

from sqlalchemy import Connection, text

from rootcause_core.db.schemas import ALL_SCHEMAS, ensure_schemas
from rootcause_core.errors import ConfigurationError
from rootcause_db.migrate import make_migration_engine

VECTOR_EXTENSION = "vector"
DEFAULT_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
DEFAULT_SEQUENCE_PRIVILEGES = "USAGE, SELECT"


def bootstrap(admin_url: str, app_role: str | None = None) -> None:
    """Create the ``vector`` extension and the schemas; grant them to ``app_role``.

    ``app_role`` receives USAGE and CREATE on each schema, plus default
    privileges on tables and sequences the admin role creates there later.
    With ``app_role=None`` only the extension and schemas are created
    (superuser-only setups).

    Raises:
        ConfigurationError: when ``admin_url`` is invalid or ``app_role`` does not exist.
        sqlalchemy.exc.SQLAlchemyError: when the database rejects a statement.
    """
    engine = make_migration_engine(admin_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE EXTENSION IF NOT EXISTS {VECTOR_EXTENSION}"))
            ensure_schemas(connection)
            if app_role is not None:
                _grant_schemas(connection, app_role)
    finally:
        engine.dispose()


def _grant_schemas(connection: Connection, role: str) -> None:
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
    ).first()
    if exists is None:
        raise ConfigurationError(
            f"App role {role!r} does not exist; create it before running bootstrap."
        )
    quote = connection.dialect.identifier_preparer.quote_identifier
    grantee = quote(role)
    for schema in ALL_SCHEMAS:
        target = quote(schema)
        for statement in (
            f"GRANT USAGE, CREATE ON SCHEMA {target} TO {grantee}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {target} "
            f"GRANT {DEFAULT_TABLE_PRIVILEGES} ON TABLES TO {grantee}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {target} "
            f"GRANT {DEFAULT_SEQUENCE_PRIVILEGES} ON SEQUENCES TO {grantee}",
        ):
            # Identifiers are quoted and escaped; nothing else is interpolated.
            # exec_driver_sql: no bind-parameter parsing, so a ':' or '%' in a
            # quoted role name stays literal.
            connection.exec_driver_sql(statement)
