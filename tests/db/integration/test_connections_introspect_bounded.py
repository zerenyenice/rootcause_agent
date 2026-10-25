"""``GET /connections/{id}/introspect`` on Postgres is bounded (RED).

Review finding HIGH 2: introspection issues a constant number of statements,
independent of the table count (no per-table round trips), still lists every
column with type and nullability, and caps the result: with more than
``introspect_max_tables`` tables (``ROOTCAUSE_INTROSPECT_MAX_TABLES``) the first
N tables in (schema, table) order are returned and ``data.truncated`` is true.
``data.truncated`` is always present (false when nothing was cut).

Review finding MEDIUM: schema and table names containing a dot introspect
correctly (they are identifiers, not ``schema.table`` strings to split).
"""

import threading
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from psycopg import sql

from tests.db.integration.conftest import CONNECTIONS_PATH, ConnectionsApi, CustomerDb

pytestmark = pytest.mark.integration

SEED_TABLES = 3  # public.big, public.orders, sales.regions
BULK_TABLES = 200
SMALL_CAP = 5
LARGE_CAP = 1000
# Generous absolute bound on driver-level statements (each may be preceded by a
# transaction-local statement_timeout setter); a per-table design needs > 400.
MAX_DRIVER_CALLS = 10
BULK_COLUMNS = [
    {"name": "id", "type": "bigint", "nullable": False},
    {"name": "label", "type": "character varying", "nullable": True},
    {"name": "at", "type": "timestamp with time zone", "nullable": False},
]


def _admin(customer: CustomerDb) -> psycopg.Connection[Any]:
    return psycopg.connect(customer.admin_url, autocommit=True)


def _create_bulk_tables(customer: CustomerDb, count: int) -> None:
    """``count`` tables ``bulk.t000``... readable by the reader role."""
    ident = sql.Identifier
    with _admin(customer) as connection:
        connection.execute("CREATE SCHEMA bulk")
        for index in range(count):
            connection.execute(
                sql.SQL(
                    "CREATE TABLE bulk.{} (id bigint NOT NULL, label varchar(20), "
                    "at timestamptz NOT NULL)"
                ).format(ident(f"t{index:03d}"))
            )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA bulk TO {}").format(ident(customer.reader))
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA bulk TO {}").format(
                ident(customer.reader)
            )
        )


class _StatementCounter:
    """Counts statements sent through psycopg cursors from any thread while active."""

    def __init__(self) -> None:
        self.calls = 0
        self.active = False
        self._lock = threading.Lock()

    def wrap(self, method: Callable[..., Any]) -> Callable[..., Any]:
        counter = self

        def counted(*args: Any, **kwargs: Any) -> Any:
            if counter.active:
                with counter._lock:
                    counter.calls += 1
            return method(*args, **kwargs)

        return counted


@pytest.fixture
def statement_counter(monkeypatch: pytest.MonkeyPatch) -> Iterator[_StatementCounter]:
    counter = _StatementCounter()
    for cls in (psycopg.Cursor, psycopg.ServerCursor):
        for name in ("execute", "executemany", "stream"):
            original = getattr(cls, name, None)
            if original is not None:
                monkeypatch.setattr(cls, name, counter.wrap(original))
    yield counter


def _introspect(api: ConnectionsApi, connection_id: str) -> dict[str, Any]:
    response = api.client.get(f"{CONNECTIONS_PATH}/{connection_id}/introspect")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _counted_introspect(
    api: ConnectionsApi, connection_id: str, counter: _StatementCounter
) -> tuple[dict[str, Any], int]:
    counter.calls, counter.active = 0, True
    try:
        data = _introspect(api, connection_id)
    finally:
        counter.active = False
    return data, counter.calls


def _table_names(data: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (schema["name"], table["name"]) for schema in data["schemas"] for table in schema["tables"]
    ]


def test_introspect_statement_count_does_not_grow_with_the_table_count(
    make_connections_api: Any, customer_db: CustomerDb, statement_counter: _StatementCounter
) -> None:
    api: ConnectionsApi = make_connections_api(introspect_max_tables=str(LARGE_CAP))
    connection_id = api.register(customer_db.reader)
    _, calls_small = _counted_introspect(api, connection_id, statement_counter)
    _create_bulk_tables(customer_db, BULK_TABLES)

    data, calls_large = _counted_introspect(api, connection_id, statement_counter)

    assert len(_table_names(data)) == SEED_TABLES + BULK_TABLES
    assert calls_large == calls_small
    assert calls_large <= MAX_DRIVER_CALLS


def test_introspect_of_many_tables_lists_every_column_with_type_and_nullability(
    make_connections_api: Any, customer_db: CustomerDb
) -> None:
    api: ConnectionsApi = make_connections_api(introspect_max_tables=str(LARGE_CAP))
    _create_bulk_tables(customer_db, BULK_TABLES)

    data = _introspect(api, api.register(customer_db.reader))

    assert data["truncated"] is False
    bulk = next(schema for schema in data["schemas"] if schema["name"] == "bulk")
    assert [table["name"] for table in bulk["tables"]] == [f"t{i:03d}" for i in range(BULK_TABLES)]
    for table in bulk["tables"]:
        assert table["columns"] == BULK_COLUMNS, table["name"]


def test_introspect_over_the_cap_returns_the_first_tables_and_flags_truncation(
    make_connections_api: Any, customer_db: CustomerDb
) -> None:
    api: ConnectionsApi = make_connections_api(introspect_max_tables=str(SMALL_CAP))
    _create_bulk_tables(customer_db, SMALL_CAP)  # 3 seed + 5 bulk = 8 > 5

    data = _introspect(api, api.register(customer_db.reader))

    assert data["truncated"] is True
    expected = sorted(
        [("public", "big"), ("public", "orders"), ("sales", "regions")]
        + [("bulk", f"t{i:03d}") for i in range(SMALL_CAP)]
    )[:SMALL_CAP]
    assert _table_names(data) == expected


def test_introspect_at_or_under_the_cap_is_not_truncated(
    make_connections_api: Any, customer_db: CustomerDb
) -> None:
    api: ConnectionsApi = make_connections_api(introspect_max_tables=str(SEED_TABLES))

    data = _introspect(api, api.register(customer_db.reader))

    assert data["truncated"] is False
    assert len(_table_names(data)) == SEED_TABLES


def test_introspect_handles_schema_and_table_names_containing_dots(
    make_connections_api: Any, customer_db: CustomerDb
) -> None:
    ident = sql.Identifier
    reader = ident(customer_db.reader)
    with _admin(customer_db) as connection:
        connection.execute('CREATE SCHEMA "a.b"')
        connection.execute('CREATE TABLE "a.b"."x.y" (v integer NOT NULL, "w.z" text)')
        connection.execute('CREATE TABLE public."x.y" (k text)')
        connection.execute(sql.SQL('GRANT USAGE ON SCHEMA "a.b" TO {}').format(reader))
        connection.execute(
            sql.SQL('GRANT SELECT ON "a.b"."x.y", public."x.y" TO {}').format(reader)
        )
    api: ConnectionsApi = make_connections_api()

    data = _introspect(api, api.register(customer_db.reader))

    schemas = {schema["name"]: schema for schema in data["schemas"]}
    assert set(schemas) == {"a.b", "public", "sales"}
    assert schemas["a.b"]["tables"] == [
        {
            "name": "x.y",
            "row_estimate": 0,
            "columns": [
                {"name": "v", "type": "integer", "nullable": False},
                {"name": "w.z", "type": "text", "nullable": True},
            ],
        }
    ]
    public = {table["name"]: table for table in schemas["public"]["tables"]}
    assert set(public) == {"big", "orders", "x.y"}
    assert public["x.y"]["columns"] == [{"name": "k", "type": "text", "nullable": True}]
