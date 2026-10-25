"""After bootstrap + migrate, every object belongs to the owner role, not to a login role."""

from typing import Any

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

SCHEMAS = ["app", "catalog", "audit"]


def _query(url: str, statement: str, **params: object) -> list[tuple[Any, ...]]:
    engine = sa.create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as connection:
            return [tuple(r) for r in connection.execute(sa.text(statement), params)]
    finally:
        engine.dispose()


def test_every_table_and_sequence_is_owned_by_the_owner_role(migrated_roles):
    rows = _query(
        migrated_roles.admin_url,
        "SELECT n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = ANY(:s) AND c.relkind IN ('r', 'S')",
        s=SCHEMAS,
    )

    assert {(s, n) for s, n, _, _ in rows} >= {
        ("app", "connections"),
        ("app", "alembic_version"),
        ("audit", "query_log"),
        ("catalog", "versions"),
    }
    assert any(kind == "S" for _, _, kind, _ in rows), "identity sequences must be covered"
    assert {owner for *_, owner in rows} == {migrated_roles.owner}


def test_trigger_functions_and_types_are_owned_by_the_owner_role(migrated_roles):
    functions = _query(
        migrated_roles.admin_url,
        "SELECT p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = ANY(:s)",
        s=SCHEMAS,
    )
    types = _query(
        migrated_roles.admin_url,
        "SELECT t.typname, pg_get_userbyid(t.typowner) FROM pg_type t "
        "JOIN pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = ANY(:s) "
        "AND t.typtype = 'e'",
        s=SCHEMAS,
    )

    assert functions, "the audit trigger function must exist"
    assert {owner for _, owner in functions} == {migrated_roles.owner}
    assert types
    assert {owner for _, owner in types} == {migrated_roles.owner}


def test_schemas_are_owned_by_the_owner_role(migrated_roles):
    rows = _query(
        migrated_roles.admin_url,
        "SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = ANY(:s)",
        s=SCHEMAS,
    )

    assert dict(rows) == dict.fromkeys(SCHEMAS, migrated_roles.owner)


def test_app_role_does_not_own_the_database(migrated_roles):
    owner = _query(
        migrated_roles.admin_url,
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()",
    )[0][0]

    assert owner not in {migrated_roles.app, migrated_roles.migrator, migrated_roles.owner}


def test_app_role_privileges_on_each_table(migrated_roles):
    def privilege(table: str, kind: str) -> bool:
        return bool(
            _query(
                migrated_roles.admin_url,
                "SELECT has_table_privilege(:r, :t, :k)",
                r=migrated_roles.app,
                t=table,
                k=kind,
            )[0][0]
        )

    for table in ("app.connections", "catalog.versions"):
        for kind in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert privilege(table, kind), (table, kind)
    assert privilege("audit.query_log", "SELECT")
    assert privilege("audit.query_log", "INSERT")
    for kind in ("UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
        assert not privilege("audit.query_log", kind), kind
    for kind in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        assert not privilege("app.alembic_version", kind), kind
