"""Read-only, locked-down, bounded DuckDB warehouse executor.

Defence in depth for agent-authored SQL:

1. Every statement passes :func:`validate_sql` with a :class:`CatalogSnapshot` of the
   warehouse's base tables (taken once at open; the file is opened read-only, so it
   cannot go stale). System views (``pg_*``, ``duckdb_*``, ``pragma_*``,
   ``sqlite_*``, ``information_schema.*``) and unknown tables are rejected. Only the
   regenerated, LIMIT-capped ``result.sql`` is executed, never the caller's text.
2. Generator functions (``range``, ``generate_series``, ``unnest``, ``repeat``) are
   rejected in any position before anything runs, because they can blow up a tiny
   input into an unbounded intermediate result.
3. The connection is opened ``read_only`` and hardened by
   :mod:`rootcause.connections.duckdb_lockdown`: no external access (files, URLs,
   ``ATTACH``), no extension auto-install/auto-load, no Python replacement scans, no
   persistent secrets, a memory limit, two threads and no temp-file spill.
   ``lock_configuration`` is set last and the values are re-read and verified (fail
   closed). The options are applied with ``SET`` rather than as connect-time config
   because DuckDB refuses a second in-process connection to the same file whose
   connect config differs. All of them are instance-wide (GLOBAL) settings that
   other in-process connections to the same file inherit, except
   ``python_enable_replacements``, which DuckDB keeps per connection: it applies to
   this executor's connection only.
4. Each execution runs under a :class:`threading.Timer` that interrupts the
   connection, and a lock serialises access to the single connection. Results are
   fetched in chunks against a byte budget, so an oversized result is cut off early.

Schema introspection (``list_tables``/``describe_table``/``distinct_values``)
looks names up in ``information_schema`` with bound parameters and only quotes
the catalog's own spelling of a name as an identifier; caller input is never
interpolated into SQL.

Exceptions raised here never carry SQL text, row values or names supplied by the
caller, so they are safe to log and to surface as tool errors: database errors are
re-raised as :class:`QueryFailed` without the original exception chained.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, NamedTuple, Self

import duckdb
from sqlglot.errors import SqlglotError

from rootcause.agents.tools.sql import QueryEstimate, QueryResult
from rootcause.connections.duckdb_guards import (
    GENERATOR_FUNCTION_CODE,
    RESULT_TOO_LARGE_CODE,
    ResultBudget,
    ResultTooLarge,
    generator_function_found,
)
from rootcause.connections.duckdb_lockdown import SETTINGS_SQL, SNAPSHOT_SETTINGS, open_locked
from rootcause.connections.rejection_guidance import guidance_for
from rootcause.connections.validation import (
    CatalogSnapshot,
    FunctionCatalog,
    IssueCode,
    validate_sql,
)
from rootcause.errors import ConfigurationError, RootCauseError
from rootcause.settings import ENV_PREFIX, Settings

DIALECT = "duckdb"
DEFAULT_MAX_ROWS = 10_000
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_DISTINCT_LIMIT = 50

DEFAULT_MEMORY_LIMIT_MB = 1024
DEFAULT_MAX_RESULT_BYTES = 16 * 1024 * 1024
WAREHOUSE_PATH_VARIABLE = f"{ENV_PREFIX}WAREHOUSE_PATH"

_FETCH_CHUNK_ROWS = 128
_CATALOG_SQL = (
    "SELECT c.table_schema, c.table_name, c.column_name "
    "FROM information_schema.columns AS c JOIN information_schema.tables AS t "
    "ON c.table_catalog = t.table_catalog AND c.table_schema = t.table_schema "
    "AND c.table_name = t.table_name "
    "WHERE t.table_catalog = current_database() AND t.table_schema = current_schema() "
    "AND t.table_type = 'BASE TABLE' ORDER BY c.table_name, c.ordinal_position"
)
_TABLES_SQL = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog = current_database() AND table_schema = current_schema() "
    "AND table_type = 'BASE TABLE' ORDER BY table_name"
)
_TABLE_LOOKUP_SQL = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog = current_database() AND table_schema = current_schema() "
    "AND table_type = 'BASE TABLE' AND table_name = ?"
)
# Every relation of the database in any schema: bare-name uniqueness checks need them all.
_RELATIONS_SQL = (
    "SELECT table_schema, table_name FROM information_schema.tables "
    "WHERE table_catalog = current_database() ORDER BY table_schema, table_name"
)
_COLUMNS_SQL = (
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_catalog = current_database() AND table_schema = current_schema() "
    "AND table_name = ? ORDER BY ordinal_position"
)
_CARDINALITY_KEY = "Estimated Cardinality"
# DuckDB errors about the statement itself (syntax, names, catalog), not the data:
# their message is safe and useful to hand back to the model as ``detail``.
INVALID_STATEMENT_ERROR_TYPES = frozenset(
    {"ParserException", "BinderException", "CatalogException"}
)
MAX_ERROR_DETAIL_CHARS = 500


class QueryRejected(RootCauseError):
    """The statement failed validation; ``codes`` lists the issue codes (no SQL text).

    ``reasons`` are the validator's per-issue messages (they name only identifiers the
    caller wrote, never row values); ``hints`` are static rewrite guidance for the codes.
    Neither is part of ``str(exc)``, so logs stay value-free.
    """

    code = "query_rejected"
    status_code = 400

    def __init__(self, codes: Sequence[str], reasons: Sequence[str] = ()) -> None:
        self.codes: tuple[str, ...] = tuple(dict.fromkeys(codes))
        self.reasons: tuple[str, ...] = tuple(dict.fromkeys(reasons))
        self.hints: tuple[str, ...] = guidance_for(self.codes)
        super().__init__(f"Query rejected by the read-only validator: {', '.join(self.codes)}.")


class QueryTimeout(RootCauseError):
    """The statement exceeded the executor's time budget and was interrupted."""

    code = "query_timeout"

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(f"Query interrupted after {timeout_seconds:g} seconds.")


class QueryFailed(RootCauseError):
    """The database raised while executing a validated statement.

    The message is generic; ``error_type`` names the DuckDB exception class. The
    original exception is never chained, since its message can embed SQL text,
    identifiers and row values. ``detail`` carries DuckDB's (truncated) message
    only for :data:`INVALID_STATEMENT_ERROR_TYPES`, so the model can correct the
    statement; it is never logged.
    """

    code = "query_failed"

    def __init__(self, error_type: str, detail: str | None = None) -> None:
        super().__init__("The warehouse query failed.")
        self.error_type = error_type
        self.detail = detail


class UnknownTable(RootCauseError):
    """The requested table or column does not exist in the warehouse catalog."""

    code = "unknown_table"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("Unknown table or column.")


def is_invalid_statement(exc: BaseException) -> bool:
    """Whether ``exc`` says the statement itself is invalid (not the data or the runtime)."""
    if isinstance(exc, QueryRejected | UnknownTable):
        return True
    return isinstance(exc, QueryFailed) and exc.error_type in INVALID_STATEMENT_ERROR_TYPES


def _query_failed(error: duckdb.Error) -> QueryFailed:
    """A :class:`QueryFailed` with DuckDB's message as ``detail`` for invalid statements only."""
    error_type = type(error).__name__
    if error_type not in INVALID_STATEMENT_ERROR_TYPES:
        return QueryFailed(error_type)
    return QueryFailed(error_type, str(error)[:MAX_ERROR_DETAIL_CHARS])


@dataclass(frozen=True, slots=True)
class TableInfo:
    """A warehouse table and its exact row count."""

    name: str
    row_count: int


class RelationName(NamedTuple):
    """A relation (table or view) of the source in any schema, as the catalog spells it."""

    schema: str
    name: str


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """A column of a warehouse table, its DuckDB type and whether it accepts NULL."""

    name: str
    data_type: str
    nullable: bool = True


@dataclass(frozen=True, slots=True)
class _Fetched:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def _quote_identifier(name: str) -> str:
    """Quote a catalog-provided name as a DuckDB identifier."""
    return '"' + name.replace('"', '""') + '"'


def _plan_cardinalities(node: Any) -> list[int]:
    """Every ``Estimated Cardinality`` in an ``EXPLAIN (FORMAT JSON)`` plan tree."""
    if isinstance(node, list):
        return [value for child in node for value in _plan_cardinalities(child)]
    if not isinstance(node, dict):
        return []
    extra = node.get("extra_info")
    raw = extra.get(_CARDINALITY_KEY) if isinstance(extra, dict) else None
    own = [int(raw)] if isinstance(raw, str | int) and str(raw).isdigit() else []
    return own + _plan_cardinalities(node.get("children", []))


class DuckDBExecutor:
    """Runs validated, read-only SQL against one DuckDB file; thread-safe."""

    def __init__(
        self,
        path: Path,
        *,
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if memory_limit_mb < 1:
            raise ValueError("memory_limit_mb must be at least 1")
        if max_result_bytes < 1:
            raise ValueError("max_result_bytes must be at least 1")
        if not Path(path).is_file():
            # read_only never creates the file; checking first keeps the path out of errors.
            raise FileNotFoundError("The warehouse database file does not exist.")
        self._max_rows = max_rows
        self._timeout = timeout_seconds
        self._max_result_bytes = max_result_bytes
        self._lock = threading.Lock()
        connection, self._catalog, self._functions = _open_with_catalog(Path(path), memory_limit_mb)
        self._connection: duckdb.DuckDBPyConnection | None = connection

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
        """Close the connection; further calls fail. Idempotent."""
        with self._lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def run(self, sql: str, purpose: str) -> QueryResult:
        """Validate and execute ``sql``; at most ``max_rows`` rows are returned.

        ``purpose`` is accepted for the tool contract and audit; it is not executed.
        """
        validated = self._validated(sql)
        fetched = self._execute(validated, (), self._max_rows + 1)
        rows = fetched.rows[: self._max_rows]
        return QueryResult(
            columns=fetched.columns,
            rows=rows,
            row_count=len(rows),
            truncated=len(fetched.rows) > self._max_rows,
            sql=validated,
        )

    def explain(self, sql: str) -> QueryEstimate:
        """Planner estimate for ``sql``: the largest node cardinality and their sum.

        Raises:
            QueryFailed: when the plan carries no cardinality estimate (fail closed).
        """
        validated = self._validated(sql)
        fetched = self._execute(f"EXPLAIN (FORMAT JSON) {validated}", (), None)
        cardinalities = _cardinalities(fetched.rows)
        if not cardinalities:
            raise QueryFailed("MissingEstimate")
        return QueryEstimate(
            estimated_rows=max(cardinalities),
            estimated_cost=float(sum(cardinalities)),
        )

    def settings_snapshot(self) -> Mapping[str, str]:
        """Current values of the lockdown settings (for verification and audit)."""
        fetched = self._execute(SETTINGS_SQL, (list(SNAPSHOT_SETTINGS),), None)
        return MappingProxyType({str(name): str(value) for name, value in fetched.rows})

    def list_tables(self) -> tuple[TableInfo, ...]:
        """Base tables in the current schema with exact row counts."""
        names = [str(row[0]) for row in self._execute(_TABLES_SQL, (), None).rows]
        return tuple(TableInfo(name, self._row_count(name)) for name in names)

    def list_relations(self) -> tuple[RelationName, ...]:
        """Every table and view of the database in every schema (not only ``main``)."""
        rows = self._execute(_RELATIONS_SQL, (), None).rows
        return tuple(RelationName(str(schema), str(name)) for schema, name in rows)

    def describe_table(self, name: str) -> tuple[ColumnInfo, ...]:
        """Columns of ``name`` in declaration order; :class:`UnknownTable` if absent."""
        table = self._resolve_table(name)
        fetched = self._execute(_COLUMNS_SQL, (table,), None)
        return tuple(
            ColumnInfo(str(col), str(dtype), str(nullable).upper() != "NO")
            for col, dtype, nullable in fetched.rows
        )

    def distinct_values(
        self, table: str, column: str, limit: int = DEFAULT_DISTINCT_LIMIT
    ) -> tuple[str, ...]:
        """Up to ``limit`` distinct non-null values of ``table.column`` as strings."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        resolved = self._resolve_table(table)
        known = {info.name for info in self.describe_table(resolved)}
        if column not in known:
            raise UnknownTable()
        col = _quote_identifier(column)
        sql = (
            # catalog-resolved names escaped by _quote_identifier; LIMIT is bound
            f"SELECT DISTINCT CAST({col} AS VARCHAR) AS value "  # nosec B608
            f"FROM {_quote_identifier(resolved)} WHERE {col} IS NOT NULL ORDER BY 1 LIMIT ?"
        )
        fetched = self._execute(sql, (min(limit, self._max_rows),), None)
        return tuple(str(row[0]) for row in fetched.rows)

    @property
    def function_catalog(self) -> FunctionCatalog:
        """Built-in and user-defined function names, read once when the file was opened."""
        return self._functions

    def _validated(self, sql: str) -> str:
        """Validate against the catalog, then reject generator functions (both fail closed)."""
        # The extra row lets ``run`` tell "exactly max_rows" apart from "more were cut".
        result = validate_sql(
            sql,
            DIALECT,
            catalog=self._catalog,
            functions=self._functions,
            default_limit=self._max_rows + 1,
        )
        if not result.ok:
            raise QueryRejected(
                [issue.code.value for issue in result.errors],
                [issue.message for issue in result.errors],
            )
        try:
            has_generator = generator_function_found(result.sql, DIALECT)
        except (SqlglotError, RecursionError):
            raise QueryRejected([IssueCode.PARSE_ERROR.value]) from None
        if has_generator:
            raise QueryRejected([GENERATOR_FUNCTION_CODE])
        return result.sql

    def _resolve_table(self, name: str) -> str:
        rows = self._execute(_TABLE_LOOKUP_SQL, (name,), None).rows
        if not rows:
            raise UnknownTable()
        return str(rows[0][0])

    def _row_count(self, table: str) -> int:
        # name read from information_schema, escaped by _quote_identifier
        sql = f"SELECT count(*) FROM {_quote_identifier(table)}"  # nosec B608
        return int(self._execute(sql, (), None).rows[0][0])

    def _execute(self, sql: str, params: Sequence[Any], limit: int | None) -> _Fetched:
        """Run one trusted statement under the lock, the interrupt timer and the byte budget."""
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("The warehouse executor is closed.")
            timer = threading.Timer(self._timeout, connection.interrupt)
            timer.daemon = True
            timer.start()
            try:
                connection.execute(sql, list(params))
                rows = self._fetch(connection, limit)
                columns = tuple(str(d[0]) for d in connection.description or ())
            except duckdb.InterruptException:
                raise QueryTimeout(self._timeout) from None
            except duckdb.Error as error:
                raise _query_failed(error) from None
            except ResultTooLarge:
                raise QueryRejected([RESULT_TOO_LARGE_CODE]) from None
            finally:
                timer.cancel()
                timer.join()
        return _Fetched(columns=columns, rows=rows)

    def _fetch(
        self, connection: duckdb.DuckDBPyConnection, limit: int | None
    ) -> tuple[tuple[Any, ...], ...]:
        """Fetch up to ``limit`` rows (all when ``None``) in chunks, charging the byte budget."""
        budget = ResultBudget(self._max_result_bytes)
        rows: list[tuple[Any, ...]] = []
        while limit is None or len(rows) < limit:
            wanted = (
                _FETCH_CHUNK_ROWS if limit is None else min(_FETCH_CHUNK_ROWS, limit - len(rows))
            )
            chunk = connection.fetchmany(wanted)
            if not chunk:
                break
            for row in chunk:
                budget.charge(row)
                rows.append(tuple(row))
        return tuple(rows)


def _cardinalities(rows: Sequence[tuple[Any, ...]]) -> list[int]:
    """Cardinalities of every ``EXPLAIN (FORMAT JSON)`` plan row; ``QueryFailed`` if unreadable."""
    try:
        plans = [json.loads(plan) for _, plan in rows]
    except (TypeError, ValueError):
        raise QueryFailed("MalformedPlan") from None
    # Looked up through the module namespace on every call (tests substitute it).
    return [value for plan in plans for value in _plan_cardinalities(plan)]


def _load_catalog(connection: duckdb.DuckDBPyConnection) -> CatalogSnapshot:
    """Schema-qualified base tables of the current schema and their columns.

    Keys are ``schema.table``: bare names resolve by their last part and ``main.t``
    matches exactly, while any other qualifier (``information_schema.tables``) does
    not fall back to a bare match.
    """
    columns: dict[str, list[str]] = {}
    for schema, table, column in connection.execute(_CATALOG_SQL).fetchall():
        columns.setdefault(f"{schema}.{table}", []).append(str(column))
    return CatalogSnapshot(columns)


def load_function_catalog(connection: duckdb.DuckDBPyConnection) -> FunctionCatalog:
    """Built-in and user-defined (macro) function names of the database, lower-cased."""
    rows = connection.execute(
        "SELECT DISTINCT lower(function_name), internal FROM duckdb_functions()"
    )
    names = rows.fetchall()
    builtin = frozenset(str(name) for name, internal in names if internal)
    user = frozenset(str(name) for name, internal in names if not internal)
    return FunctionCatalog(builtin=builtin, user=user)


def _open_with_catalog(
    path: Path, memory_limit_mb: int
) -> tuple[duckdb.DuckDBPyConnection, CatalogSnapshot, FunctionCatalog]:
    connection = open_locked(str(path), memory_limit_mb)
    try:
        return connection, _load_catalog(connection), load_function_catalog(connection)
    except BaseException:
        connection.close()
        raise


def executor_from_settings(settings: Settings) -> DuckDBExecutor | None:
    """The warehouse executor configured by ``ROOTCAUSE_WAREHOUSE_PATH``, or ``None``.

    Raises:
        ConfigurationError: when the variable points at a file that does not exist
            (the message names the variable, never the path).
    """
    path = settings.warehouse_path
    if path is None:
        return None
    if not path.is_file():
        raise ConfigurationError(
            f"{WAREHOUSE_PATH_VARIABLE} does not point at an existing DuckDB file.",
            variables=(WAREHOUSE_PATH_VARIABLE,),
        )
    return DuckDBExecutor(path)
