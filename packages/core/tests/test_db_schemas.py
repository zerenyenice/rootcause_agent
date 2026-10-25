"""Schema name constants and ``ensure_schemas`` for the application database."""

from unittest.mock import MagicMock


def test_schema_constants_have_the_documented_names():
    from rootcause_core.db import schemas

    assert schemas.APP_SCHEMA == "app"
    assert schemas.CATALOG_SCHEMA == "catalog"
    assert schemas.AUDIT_SCHEMA == "audit"


def test_ensure_schemas_creates_each_schema_idempotently():
    from rootcause_core.db.schemas import APP_SCHEMA, AUDIT_SCHEMA, CATALOG_SCHEMA, ensure_schemas

    connection = MagicMock()

    ensure_schemas(connection)

    statements = [str(call.args[0]).upper() for call in connection.execute.call_args_list]
    for schema in (APP_SCHEMA, CATALOG_SCHEMA, AUDIT_SCHEMA):
        matching = [s for s in statements if schema.upper() in s]
        assert len(matching) == 1, f"expected exactly one statement for {schema}"
        assert "CREATE SCHEMA IF NOT EXISTS" in matching[0]
