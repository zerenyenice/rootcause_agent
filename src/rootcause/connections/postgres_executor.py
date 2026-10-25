"""Read-only, bounded executor for a customer Postgres database (psycopg 3).

Same surface as :class:`~rootcause.connections.duckdb_executor.DuckDBExecutor`
(the ``QueryExecutor`` and schema-tool protocols). Two independent write barriers
(ADR-0002):

1. Every statement passes :func:`validate_sql` (dialect ``postgres``) against a
   :class:`CatalogSnapshot` of the role's user relations, taken at connect time:
   one read-only statement, allow-listed functions, a capped ``LIMIT``; system
   catalogs and unknown tables are rejected, as are generator functions. Only the
   regenerated ``result.sql`` is executed, never the caller's text.
2. Every statement runs inside its own ``READ ONLY`` transaction with a
   transaction-local ``statement_timeout``, over the extended query protocol (one
   statement per call), so a write fails on the server even if the validator is
   bypassed.

Connection, catalog and execution errors never carry the password, the DSN, SQL
text or row values, and the driver exception is never chained.
"""

from __future__ import annotations

import threading
from collections.abc import Generator, Iterator, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import psycopg
from psycopg import sql
from sqlglot.errors import SqlglotError

from rootcause.agents.tools.sql import QueryEstimate, QueryResult
from rootcause.connections import postgres_queries as q
from rootcause.connections.duckdb_executor import (
    ColumnInfo,
    QueryFailed,
    QueryRejected,
    QueryTimeout,
    TableInfo,
    UnknownTable,
)
from rootcause.connections.duckdb_guards import (
    GENERATOR_FUNCTION_CODE,
    RESULT_TOO_LARGE_CODE,
    ResultBudget,
    ResultTooLarge,
    generator_function_found,
)
from rootcause.connections.validation import CatalogSnapshot, IssueCode, validate_sql
from rootcause.errors import RootCauseError

__all__ = [
    "DIALECT",
    "ColumnInfo",
    "ConnectionFailed",
    "PostgresExecutor",
    "QueryFailed",
    "QueryRejected",
    "QueryTimeout",
    "TableInfo",
    "UnknownTable",
]

DIALECT = "postgres"
DEFAULT_MAX_ROWS = 10_000
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_DISTINCT_LIMIT = 50
DEFAULT_MAX_RESULT_BYTES = 16 * 1024 * 1024
APPLICATION_NAME = "rootcause"
# Errors about the statement itself (syntax, names, types), not about data or privileges:
# their primary message is safe and useful to hand back to the model as ``detail``.
INVALID_STATEMENT_ERROR_TYPES = frozenset(
    {
        "SyntaxError",
        "UndefinedTable",
        "UndefinedColumn",
        "UndefinedFunction",
        "UndefinedObject",
        "AmbiguousColumn",
        "AmbiguousFunction",
        "AmbiguousAlias",
        "GroupingError",
        "DatatypeMismatch",
        "WrongObjectType",
        "InvalidColumnReference",
    }
)
MAX_ERROR_DETAIL_CHARS = 500
# libpq >= 17 can stream rows in chunks; older ones only one row at a time.
_CHUNKED_STREAMING_LIBPQ = 170000
_STREAM_CHUNK_ROWS = 128 if psycopg.pq.version() >= _CHUNKED_STREAMING_LIBPQ else 1


class ConnectionFailed(RootCauseError):
    """The server could not be reached or refused the login (no host, user or DSN inside)."""

    code = "connection_failed"
    status_code = 503
    public_message = "Could not connect to the database."

    def __init__(self) -> None:
        super().__init__("Could not connect to the Postgres database.")


@dataclass(frozen=True, slots=True)
class _Fetched:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def _query_failed(error: psycopg.Error) -> QueryFailed:
    """A :class:`QueryFailed` with the server's primary message for invalid statements only."""
    error_type = type(error).__name__
    if error_type not in INVALID_STATEMENT_ERROR_TYPES:
        return QueryFailed(error_type)
    message = error.diag.message_primary or ""
    return QueryFailed(error_type, message[:MAX_ERROR_DETAIL_CHARS] or None)


def _plan_rows(node: Any) -> list[int]:
    """Every ``Plan Rows`` estimate in an ``EXPLAIN (FORMAT JSON)`` plan tree."""
    if isinstance(node, list):
        return [value for child in node for value in _plan_rows(child)]
    if not isinstance(node, dict):
        return []
    own = node.get("Plan Rows")
    rows = [int(own)] if isinstance(own, int | float) else []
    children = [node[key] for key in ("Plan", "Plans") if key in node]
    return rows + [value for child in children for value in _plan_rows(child)]


def _top_cost(plan: Any) -> float | None:
    try:
        return float(plan[0]["Plan"]["Total Cost"])
    except (LookupError, TypeError, ValueError):
        return None


def _split_name(name: str) -> tuple[str | None, str] | None:
    """``schema.table`` or a bare ``table``; anything else is no valid name."""
    parts = name.split(".")
    if len(parts) == 1 and parts[0]:
        return None, parts[0]
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    return None


class PostgresExecutor:
    """Runs validated SQL in read-only transactions on one connection; thread-safe."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        sslmode: str,
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if connect_timeout_seconds < 1:
            raise ValueError("connect_timeout_seconds must be at least 1")
        if max_result_bytes < 1:
            raise ValueError("max_result_bytes must be at least 1")
        self._max_rows = max_rows
        self._timeout_ms = max(1, round(timeout_seconds * 1000))
        self._timeout_seconds = timeout_seconds
        self._max_result_bytes = max_result_bytes
        self._lock = threading.Lock()
        self._connection: psycopg.Connection[tuple[Any, ...]] | None = _connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            sslmode=sslmode,
            connect_timeout=connect_timeout_seconds,
        )
        try:
            self._catalog = self._load_catalog()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection; further calls raise ``RuntimeError``. Idempotent."""
        with self._lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    # --- QueryExecutor ------------------------------------------------------------

    def run(self, sql_text: str, purpose: str) -> QueryResult:
        """Validate and execute ``sql_text``; at most ``max_rows`` rows are returned.

        ``purpose`` is accepted for the tool contract and audit; it is not executed.
        """
        validated = self._validated(sql_text)
        fetched = self._execute(validated, None, self._max_rows + 1)
        rows = fetched.rows[: self._max_rows]
        return QueryResult(
            columns=fetched.columns,
            rows=rows,
            row_count=len(rows),
            truncated=len(fetched.rows) > self._max_rows,
            sql=validated,
        )

    def explain(self, sql_text: str) -> QueryEstimate:
        """Planner estimate: the largest node row estimate and the top node's total cost.

        Raises:
            QueryFailed: when the plan carries no estimate (fail closed).
        """
        validated = self._validated(sql_text)
        fetched = self._execute(f"EXPLAIN (FORMAT JSON) {validated}", None, None)
        plan = fetched.rows[0][0] if fetched.rows else None
        rows, cost = _plan_rows(plan), _top_cost(plan)
        if not rows or cost is None:
            raise QueryFailed("MissingEstimate")
        return QueryEstimate(estimated_rows=max(rows), estimated_cost=cost)

    # --- schema tools ---------------------------------------------------------------

    def list_tables(self) -> tuple[TableInfo, ...]:
        """User relations as ``schema.table`` with the planner's row estimate (``reltuples``)."""
        fetched = self._execute(q.TABLES_SQL, None, None)
        return tuple(
            TableInfo(f"{schema}.{table}", int(rows)) for schema, table, rows in fetched.rows
        )

    def describe_table(self, name: str) -> tuple[ColumnInfo, ...]:
        """Columns of ``schema.table`` (or an unambiguous bare name) in declaration order."""
        oid, _, _ = self._resolve_table(name)
        fetched = self._execute(q.COLUMNS_SQL, {"oid": oid}, None)
        return tuple(
            ColumnInfo(str(column), str(data_type), bool(nullable))
            for column, data_type, nullable in fetched.rows
        )

    def distinct_values(
        self, table: str, column: str, limit: int = DEFAULT_DISTINCT_LIMIT
    ) -> tuple[str, ...]:
        """Up to ``limit`` distinct non-null values of ``table.column`` as sorted strings."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        oid, schema, relation = self._resolve_table(table)
        columns = self._execute(q.COLUMNS_SQL, {"oid": oid}, None).rows
        if column not in {str(row[0]) for row in columns}:
            raise UnknownTable()
        col = sql.Identifier(column)
        statement = sql.SQL(
            "SELECT DISTINCT CAST({col} AS text) AS value FROM {schema}.{table} "
            "WHERE {col} IS NOT NULL ORDER BY 1 LIMIT %(limit)s"
        ).format(col=col, schema=sql.Identifier(schema), table=sql.Identifier(relation))
        fetched = self._execute(statement, {"limit": min(limit, self._max_rows)}, None)
        return tuple(str(row[0]) for row in fetched.rows)

    # --- connection probe -------------------------------------------------------------

    def server_version(self) -> str:
        """The server's version number, e.g. ``16.4``."""
        rows = self._execute(q.SERVER_VERSION_SQL, None, None).rows
        return str(rows[0][0]).split()[0]

    def is_read_only(self) -> bool:
        """Whether the role can change nothing (see ``postgres_queries.CAN_WRITE_SQL``)."""
        rows = self._execute(q.CAN_WRITE_SQL, None, None).rows
        return rows[0][0] is False

    # --- internals ----------------------------------------------------------------------

    def _validated(self, sql_text: str) -> str:
        """Validate against the catalog, then reject generator functions (both fail closed)."""
        # The extra row lets ``run`` tell "exactly max_rows" apart from "more were cut".
        result = validate_sql(
            sql_text, DIALECT, catalog=self._catalog, default_limit=self._max_rows + 1
        )
        if not result.ok:
            raise QueryRejected([issue.code.value for issue in result.errors])
        try:
            has_generator = generator_function_found(result.sql, DIALECT)
        except (SqlglotError, RecursionError):
            raise QueryRejected([IssueCode.PARSE_ERROR.value]) from None
        if has_generator:
            raise QueryRejected([GENERATOR_FUNCTION_CODE])
        return result.sql

    def _load_catalog(self) -> CatalogSnapshot:
        columns: dict[str, list[str]] = {}
        for schema, table, column in self._execute(q.CATALOG_SQL, None, None).rows:
            columns.setdefault(f"{schema}.{table}", []).append(str(column))
        return CatalogSnapshot(columns)

    def _resolve_table(self, name: str) -> tuple[int, str, str]:
        parsed = _split_name(name)
        if parsed is None:
            raise UnknownTable()
        schema, table = parsed
        rows = self._execute(q.TABLE_LOOKUP_SQL, {"schema": schema, "table": table}, None).rows
        if len(rows) != 1:  # unknown, or a bare name found in several schemas
            raise UnknownTable()
        oid, resolved_schema, relation = rows[0]
        return int(oid), str(resolved_schema), str(relation)

    def _execute(
        self, statement: str | sql.Composed, params: Mapping[str, Any] | None, limit: int | None
    ) -> _Fetched:
        """Run one trusted statement in a READ ONLY transaction with the statement timeout."""
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("The Postgres executor is closed.")
            try:
                with connection.transaction():
                    connection.execute(q.STATEMENT_TIMEOUT_SQL, {"timeout": f"{self._timeout_ms}"})
                    with connection.cursor() as cursor:
                        rows = self._fetch(
                            cursor.stream(statement, params, size=_STREAM_CHUNK_ROWS), limit
                        )
                        columns = tuple(str(d.name) for d in cursor.description or ())
            except psycopg.errors.QueryCanceled:
                raise QueryTimeout(self._timeout_seconds) from None
            except psycopg.Error as error:
                raise _query_failed(error) from None
            except ResultTooLarge:
                raise QueryRejected([RESULT_TOO_LARGE_CODE]) from None
        return _Fetched(columns=columns, rows=rows)

    def _fetch(
        self, stream: Iterator[tuple[Any, ...]], limit: int | None
    ) -> tuple[tuple[Any, ...], ...]:
        """Take up to ``limit`` rows (all when ``None``), charging the byte budget."""
        budget = ResultBudget(self._max_result_bytes)
        rows: list[tuple[Any, ...]] = []
        try:
            for row in stream:
                if limit is not None and len(rows) >= limit:
                    break
                budget.charge(row)
                rows.append(tuple(row))
        finally:
            if isinstance(stream, Generator):  # psycopg's stream: drain/cancel on early stop
                stream.close()
        return tuple(rows)


def _connect(**params: Any) -> psycopg.Connection[tuple[Any, ...]]:
    """Open the connection; failures become :class:`ConnectionFailed` with nothing chained."""
    try:
        connection = psycopg.connect(application_name=APPLICATION_NAME, **params)
    except (psycopg.Error, ValueError, OSError):
        connection = None
    if connection is None:
        # Raised outside the handler: libpq messages name the host, database and user.
        raise ConnectionFailed()
    connection.read_only = True  # every transaction starts as BEGIN READ ONLY
    return connection
