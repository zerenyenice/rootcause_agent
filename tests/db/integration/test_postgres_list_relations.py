"""``PostgresExecutor.list_relations`` against a real server: every relation, readable or not."""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from rootcause.connections.duckdb_executor import RelationName
from rootcause.connections.postgres_executor import PostgresExecutor

pytestmark = pytest.mark.integration


@pytest.fixture
def executor(customer_db: Any) -> Iterator[PostgresExecutor]:
    ex = PostgresExecutor(**customer_db.params())
    try:
        yield ex
    finally:
        ex.close()


def test_lists_user_relations_across_schemas_sorted(executor: PostgresExecutor) -> None:
    relations = executor.list_relations()

    assert all(isinstance(r, RelationName) for r in relations)
    assert [(r.schema, r.name) for r in relations if r.name in {"big", "orders", "regions"}] == [
        ("public", "big"),
        ("public", "orders"),
        ("sales", "regions"),
    ]


def test_excludes_system_schemas(executor: PostgresExecutor) -> None:
    schemas = {r.schema for r in executor.list_relations()}

    assert "information_schema" not in schemas
    assert not any(schema.startswith("pg_") for schema in schemas)


def test_includes_relations_the_role_cannot_read(
    customer_db: Any, executor: PostgresExecutor
) -> None:
    with psycopg.connect(customer_db.admin_url, autocommit=True) as admin:
        admin.execute("CREATE SCHEMA locked")
        admin.execute("CREATE TABLE locked.orders (id integer)")
        admin.execute("CREATE VIEW public.orders_view AS SELECT id FROM public.orders")

    pairs = {(r.schema, r.name) for r in executor.list_relations()}

    # a bare name shared with an unreadable table must still be seen as ambiguous
    assert ("locked", "orders") in pairs
    assert ("public", "orders_view") in pairs
