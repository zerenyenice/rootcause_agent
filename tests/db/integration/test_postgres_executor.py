"""Read-only Postgres executor against a real server (RED).

Same surface as ``DuckDBExecutor`` (the ``QueryExecutor`` and schema-tool
protocols): ``run``, ``explain``, ``list_tables``, ``describe_table``,
``distinct_values``. Two independent write barriers (ADR-0002): the sqlglot
validator (dialect ``postgres``) rejects writes before they reach the server,
and every statement runs in a ``READ ONLY`` transaction with a
``statement_timeout``, which blocks a write even when the validator is bypassed.
"""

from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest

from rootcause.connections import postgres_executor as module
from rootcause.connections.postgres_executor import (
    PostgresExecutor,
    QueryFailed,
    QueryRejected,
    QueryTimeout,
    UnknownTable,
)
from rootcause.connections.validation import ValidationResult

pytestmark = pytest.mark.integration

ORDERS_ROWS = 300
BIG_ROWS = 2000


def _admin(customer_db: Any) -> psycopg.Connection:
    return psycopg.connect(customer_db.admin_url, autocommit=True)


def _scalar(customer_db: Any, query: str) -> Any:
    with _admin(customer_db) as connection:
        row = connection.execute(query).fetchone()
    assert row is not None
    return row[0]


def _table_exists(customer_db: Any, name: str) -> bool:
    return bool(_scalar(customer_db, f"SELECT to_regclass('{name}') IS NOT NULL"))


@pytest.fixture
def make_executor(customer_db: Any) -> Iterator[Callable[..., PostgresExecutor]]:
    opened: list[PostgresExecutor] = []

    def _make(user: str | None = None, **limits: Any) -> PostgresExecutor:
        executor = PostgresExecutor(**customer_db.params(user), **limits)
        opened.append(executor)
        return executor

    yield _make
    for executor in opened:
        executor.close()


@pytest.fixture
def executor(make_executor: Callable[..., PostgresExecutor]) -> PostgresExecutor:
    return make_executor()


# --- run ----------------------------------------------------------------------


def test_run_returns_rows_columns_and_the_validated_limited_sql(executor: PostgresExecutor) -> None:
    result = executor.run("SELECT id, status FROM orders WHERE id <= 3 ORDER BY id", "probe")

    assert result.columns == ("id", "status")
    assert result.rows == ((1, "open"), (2, "void"), (3, "closed"))
    assert result.row_count == 3
    assert result.truncated is False
    assert "LIMIT" in result.sql.upper()


def test_run_accepts_schema_qualified_tables_in_other_schemas(executor: PostgresExecutor) -> None:
    result = executor.run("SELECT code FROM sales.regions ORDER BY code", "probe")

    assert result.rows == (("DE",), ("TR",))


def test_run_caps_rows_at_max_rows_and_flags_truncation(
    make_executor: Callable[..., PostgresExecutor],
) -> None:
    result = make_executor(max_rows=50).run("SELECT id FROM orders", "scan")

    assert result.row_count == 50
    assert len(result.rows) == 50
    assert result.truncated is True


def test_run_with_exactly_max_rows_is_not_truncated(
    make_executor: Callable[..., PostgresExecutor],
) -> None:
    result = make_executor(max_rows=ORDERS_ROWS).run("SELECT id FROM orders", "scan")

    assert result.row_count == ORDERS_ROWS
    assert result.truncated is False


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (id, amount) VALUES (100000, 1)",
        "UPDATE orders SET amount = 0",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "CREATE TABLE pwned (i integer)",
        "TRUNCATE orders",
        "SELECT * INTO pwned FROM orders",
        "SELECT 1; CREATE TABLE pwned (i integer)",
        "SELECT nextval('orders_id_seq')",
        "SELECT pg_advisory_lock(1)",
    ],
)
def test_writes_are_rejected_by_the_validator_and_change_nothing(
    executor: PostgresExecutor, customer_db: Any, sql: str
) -> None:
    with pytest.raises(QueryRejected) as caught:
        executor.run(sql, "attack")

    assert caught.value.codes
    assert _scalar(customer_db, "SELECT count(*) FROM orders") == ORDERS_ROWS
    assert _scalar(customer_db, "SELECT sum(amount) > 0 FROM orders") is True
    assert not _table_exists(customer_db, "public.pwned")


def test_multi_statement_create_is_rejected_as_multiple_statements(
    executor: PostgresExecutor, customer_db: Any
) -> None:
    with pytest.raises(QueryRejected) as caught:
        executor.run("SELECT 1; CREATE TABLE pwned (i integer)", "attack")

    assert "multiple_statements" in caught.value.codes
    assert not _table_exists(customer_db, "public.pwned")


@pytest.mark.parametrize(
    "sql",
    ["SELECT * FROM pg_catalog.pg_authid", "SELECT * FROM information_schema.tables"],
)
def test_system_catalogs_are_rejected(executor: PostgresExecutor, sql: str) -> None:
    with pytest.raises(QueryRejected):
        executor.run(sql, "snoop")


@pytest.mark.parametrize(
    "write_sql",
    [
        "INSERT INTO orders (id, amount) VALUES (100000, 1) RETURNING id",
        "CREATE TABLE pwned (i integer)",
    ],
)
def test_read_only_transaction_blocks_a_write_that_bypasses_the_validator(
    make_executor: Callable[..., PostgresExecutor],
    customer_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    write_sql: str,
) -> None:
    # Connect as the superuser: only the READ ONLY transaction can stop this write.
    executor = make_executor(customer_db.admin_user)

    def _validator_bug(sql: str, dialect: str, **_: Any) -> ValidationResult:
        return ValidationResult(ok=True, sql=write_sql)

    monkeypatch.setattr(module, "validate_sql", _validator_bug)

    with pytest.raises(QueryFailed) as caught:
        executor.run("SELECT 1", "bypass")

    assert caught.value.error_type == "ReadOnlySqlTransaction"
    assert _scalar(customer_db, "SELECT count(*) FROM orders") == ORDERS_ROWS
    assert not _table_exists(customer_db, "public.pwned")


def test_statement_timeout_interrupts_and_the_executor_stays_usable(
    make_executor: Callable[..., PostgresExecutor],
) -> None:
    executor = make_executor(timeout_seconds=0.5)

    with pytest.raises(QueryTimeout):
        executor.run("SELECT count(*) FROM big AS a, big AS b, big AS c", "slow")

    assert executor.run("SELECT count(*) AS n FROM orders", "after").rows == ((ORDERS_ROWS,),)


def test_runtime_errors_raise_query_failed_without_sql_text(executor: PostgresExecutor) -> None:
    with pytest.raises(QueryFailed) as caught:
        executor.run("SELECT amount / 0 AS boom FROM orders", "divide")

    assert "boom" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_executor_is_a_context_manager_and_close_is_idempotent(customer_db: Any) -> None:
    with PostgresExecutor(**customer_db.params()) as executor:
        assert executor.run("SELECT 1 AS one", "ping").rows == ((1,),)

    executor.close()
    with pytest.raises(RuntimeError):
        executor.run("SELECT 1 AS one", "closed")


def test_failed_login_raises_connection_failed_without_the_password(customer_db: Any) -> None:
    params = {**customer_db.params(), "database": "missing_db_for_connection_test"}

    with pytest.raises(module.ConnectionFailed) as caught:
        PostgresExecutor(**params)

    assert customer_db.password not in repr(caught.value) + str(caught.value)


# --- explain --------------------------------------------------------------------


def test_explain_returns_planner_estimates(executor: PostgresExecutor) -> None:
    estimate = executor.explain("SELECT * FROM orders")

    assert estimate.estimated_rows >= ORDERS_ROWS
    assert estimate.estimated_cost > 0


def test_explain_rejects_writes(executor: PostgresExecutor) -> None:
    with pytest.raises(QueryRejected):
        executor.explain("DELETE FROM orders")


# --- schema introspection -------------------------------------------------------


def test_list_tables_returns_schema_qualified_names_with_row_estimates(
    executor: PostgresExecutor,
) -> None:
    tables = {table.name: table.row_count for table in executor.list_tables()}

    assert tables == {"public.big": BIG_ROWS, "public.orders": ORDERS_ROWS, "sales.regions": 2}


def test_describe_table_returns_columns_types_and_nullability(executor: PostgresExecutor) -> None:
    columns = executor.describe_table("public.orders")

    assert [(c.name, c.data_type, c.nullable) for c in columns] == [
        ("id", "integer", False),
        ("amount", "numeric", False),
        ("status", "text", True),
    ]


def test_describe_table_resolves_an_unambiguous_bare_name(executor: PostgresExecutor) -> None:
    assert [c.name for c in executor.describe_table("regions")] == ["code", "name"]


@pytest.mark.parametrize(
    "name",
    [
        "missing",
        "pg_catalog.pg_authid",
        "information_schema.tables",
        "orders'; DROP TABLE orders; --",
    ],
)
def test_describe_table_rejects_unknown_and_system_tables(
    executor: PostgresExecutor, customer_db: Any, name: str
) -> None:
    with pytest.raises(UnknownTable):
        executor.describe_table(name)

    assert _table_exists(customer_db, "public.orders")


def test_distinct_values_returns_sorted_strings(executor: PostgresExecutor) -> None:
    assert executor.distinct_values("public.orders", "status") == ("closed", "open", "void")
    assert executor.distinct_values("orders", "status", limit=2) == ("closed", "open")


def test_distinct_values_rejects_unknown_columns(executor: PostgresExecutor) -> None:
    with pytest.raises(UnknownTable):
        executor.distinct_values("orders", "nope")
