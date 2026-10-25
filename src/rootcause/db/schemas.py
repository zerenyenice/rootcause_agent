"""Postgres schema names of the application database."""

from sqlalchemy import Connection, text

APP_SCHEMA = "app"
CATALOG_SCHEMA = "catalog"
AUDIT_SCHEMA = "audit"

ALL_SCHEMAS = (APP_SCHEMA, CATALOG_SCHEMA, AUDIT_SCHEMA)


def ensure_schemas(connection: Connection) -> None:
    """Create the application schemas if they do not exist yet."""
    for schema in ALL_SCHEMAS:
        # Names come from the constants above, never from input.
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
