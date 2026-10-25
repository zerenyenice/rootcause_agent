"""Read-only, locked-down DuckDB warehouse executor.

Defence in depth for agent-authored SQL:

1. Every statement passes :func:`validate_sql`; only the regenerated, LIMIT-capped
   ``result.sql`` is executed, never the caller's text.
2. The connection is opened ``read_only``; external access (files, URLs,
   ``ATTACH``), extension auto-install/auto-load and Python replacement scans are
   then disabled, ``lock_configuration`` is set last so no statement can undo it,
   and the resulting settings are re-read and verified (fail closed). The options
   are applied with ``SET`` rather than as connect-time config because DuckDB
   refuses a second in-process connection to the same file whose connect config
   differs; they are GLOBAL settings, so such connections inherit the lockdown.
3. Each execution runs under a :class:`threading.Timer` that interrupts the
   connection, and a lock serialises access to the single connection.

Schema introspection (``list_tables``/``describe_table``/``distinct_values``)
looks names up in ``information_schema`` with bound parameters and only quotes
the catalog's own spelling of a name as an identifier; caller input is never
interpolated into SQL.

Exceptions raised here never carry SQL text, row values or names supplied by the
caller, so they are safe to log and to surface as tool errors.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, Self

import duckdb

from rootcause.agents.tools.sql import QueryEstimate, QueryResult
from rootcause.connections.validation import validate_sql
from rootcause.errors import ConfigurationError, RootCauseError
from rootcause.settings import ENV_PREFIX, Settings

DIALECT = "duckdb"
DEFAULT_MAX_ROWS = 10_000
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_DISTINCT_LIMIT = 50
WAREHOUSE_PATH_VARIABLE = f"{ENV_PREFIX}WAREHOUSE_PATH"

# Hardening applied right after connecting; ``lock_configuration`` is set last.
_LOCKDOWN: Mapping[str, str] = MappingProxyType(
    {
        "enable_external_access": "false",
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "python_enable_replacements": "false",
    }
)
_LOCK_SETTING = "lock_configuration"
_SNAPSHOT_SETTINGS = (*_LOCKDOWN, _LOCK_SETTING)
_EXPECTED_SETTINGS: Mapping[str, str] = MappingProxyType({**_LOCKDOWN, _LOCK_SETTING: "true"})
_SETTINGS_SQL = "SELECT name, value FROM duckdb_settings() WHERE name IN ?"
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
_COLUMNS_SQL = (
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_catalog = current_database() AND table_schema = current_schema() "
    "AND table_name = ? ORDER BY ordinal_position"
)
_CARDINALITY_KEY = "Estimated Cardinality"


class QueryRejected(RootCauseError):
    """The statement failed validation; ``codes`` lists the issue codes (no SQL text)."""

    code = "query_rejected"
    status_code = 400

    def __init__(self, codes: Sequence[str]) -> None:
        self.codes: tuple[str, ...] = tuple(dict.fromkeys(codes))
        super().__init__(f"Query rejected by the read-only validator: {', '.join(self.codes)}.")


class QueryTimeout(RootCauseError):
    """The statement exceeded the executor's time budget and was interrupted."""

    code = "query_timeout"

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(f"Query interrupted after {timeout_seconds:g} seconds.")


class UnknownTable(RootCauseError):
    """The requested table or column does not exist in the warehouse catalog."""

    code = "unknown_table"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("Unknown table or column.")


@dataclass(frozen=True, slots=True)
class TableInfo:
    """A warehouse table and its exact row count."""

    name: str
    row_count: int


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """A column of a warehouse table and its DuckDB type."""

    name: str
    data_type: str


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
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not Path(path).is_file():
            # read_only never creates the file; checking first keeps the path out of errors.
            raise FileNotFoundError("The warehouse database file does not exist.")
        self._max_rows = max_rows
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._connection: duckdb.DuckDBPyConnection | None = _open_locked(Path(path))

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
        """Planner estimate for ``sql``: the largest node cardinality and their sum."""
        validated = self._validated(sql)
        fetched = self._execute(f"EXPLAIN (FORMAT JSON) {validated}", (), None)
        cardinalities = [
            value for _, plan in fetched.rows for value in _plan_cardinalities(json.loads(plan))
        ]
        return QueryEstimate(
            estimated_rows=max(cardinalities, default=0),
            estimated_cost=float(sum(cardinalities)),
        )

    def settings_snapshot(self) -> Mapping[str, str]:
        """Current values of the lockdown settings (for verification and audit)."""
        fetched = self._execute(_SETTINGS_SQL, (list(_SNAPSHOT_SETTINGS),), None)
        return MappingProxyType({str(name): str(value) for name, value in fetched.rows})

    def list_tables(self) -> tuple[TableInfo, ...]:
        """Base tables in the current schema with exact row counts."""
        names = [str(row[0]) for row in self._execute(_TABLES_SQL, (), None).rows]
        return tuple(TableInfo(name, self._row_count(name)) for name in names)

    def describe_table(self, name: str) -> tuple[ColumnInfo, ...]:
        """Columns of ``name`` in declaration order; :class:`UnknownTable` if absent."""
        table = self._resolve_table(name)
        fetched = self._execute(_COLUMNS_SQL, (table,), None)
        return tuple(ColumnInfo(str(col), str(dtype)) for col, dtype in fetched.rows)

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
            f"SELECT DISTINCT CAST({col} AS VARCHAR) AS value "
            f"FROM {_quote_identifier(resolved)} WHERE {col} IS NOT NULL ORDER BY 1 LIMIT ?"
        )
        fetched = self._execute(sql, (min(limit, self._max_rows),), None)
        return tuple(str(row[0]) for row in fetched.rows)

    def _validated(self, sql: str) -> str:
        # The extra row lets ``run`` tell "exactly max_rows" apart from "more were cut".
        result = validate_sql(sql, DIALECT, default_limit=self._max_rows + 1)
        if not result.ok:
            raise QueryRejected([issue.code.value for issue in result.errors])
        return result.sql

    def _resolve_table(self, name: str) -> str:
        rows = self._execute(_TABLE_LOOKUP_SQL, (name,), None).rows
        if not rows:
            raise UnknownTable()
        return str(rows[0][0])

    def _row_count(self, table: str) -> int:
        sql = f"SELECT count(*) FROM {_quote_identifier(table)}"
        return int(self._execute(sql, (), None).rows[0][0])

    def _execute(self, sql: str, params: Sequence[Any], limit: int | None) -> _Fetched:
        """Run one trusted statement under the lock and the interrupt timer."""
        with self._lock:
            connection = self._connection
            if connection is None:
                raise RuntimeError("The warehouse executor is closed.")
            timer = threading.Timer(self._timeout, connection.interrupt)
            timer.daemon = True
            timer.start()
            try:
                connection.execute(sql, list(params))
                raw = connection.fetchall() if limit is None else connection.fetchmany(limit)
                columns = tuple(str(d[0]) for d in connection.description or ())
            except duckdb.InterruptException:
                raise QueryTimeout(self._timeout) from None
            finally:
                timer.cancel()
                timer.join()
        return _Fetched(columns=columns, rows=tuple(tuple(row) for row in raw))


def _current_settings(connection: duckdb.DuckDBPyConnection) -> dict[str, str]:
    rows = connection.execute(_SETTINGS_SQL, [list(_SNAPSHOT_SETTINGS)]).fetchall()
    return {str(name): str(value).lower() for name, value in rows}


def _apply_lockdown(connection: duckdb.DuckDBPyConnection) -> None:
    """Disable risky features, lock the configuration last, then verify (fail closed).

    Setting names and values are module constants; nothing here comes from input.
    An already locked instance with the hardened values is accepted unchanged.
    """
    current = _current_settings(connection)
    for name, value in _LOCKDOWN.items():
        if current.get(name) != value:
            connection.execute(f"SET {name} = {value}")
    if current.get(_LOCK_SETTING) != "true":
        connection.execute(f"SET {_LOCK_SETTING} = true")
    if _current_settings(connection) != dict(_EXPECTED_SETTINGS):
        raise RuntimeError("The warehouse connection could not be locked down.")


def _open_locked(path: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _apply_lockdown(connection)
    except BaseException:
        connection.close()
        raise
    return connection


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
