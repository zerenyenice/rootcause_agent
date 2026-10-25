"""An executor restricted to the catalog's SELECTED tables and columns (Sensitive).

:class:`SelectionExecutor` wraps a connection's read-only executor (DuckDB or
Postgres). Before the inner executor sees a statement, it is validated against a
:class:`CatalogSnapshot` holding only the selected tables and their selected
columns: an unselected table is ``unknown_table``, an unselected column
``unknown_column``. A ``*`` (other than ``count(*)``) over a table with hidden
columns is rejected too, since it would expand to them. The inner executor then
validates again against the full source catalog and runs only its own
regenerated SQL. The schema tools (``list_tables``/``describe_table``/
``distinct_values``) see only the selection. The selection is fixed at build time.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from rootcause.agents.tools.sql import QueryEstimate, QueryResult
from rootcause.connections.duckdb_executor import ColumnInfo, QueryRejected, TableInfo, UnknownTable
from rootcause.connections.validation import CatalogSnapshot, validate_sql

Dialect = Literal["duckdb", "postgres"]
# A ``*`` would expand to columns the selection hides.
HIDDEN_COLUMNS_CODE = "star_over_hidden_columns"
DUCKDB_DEFAULT_SCHEMA = "main"


class SourceExecutor(Protocol):
    """What the restricted executor needs from a connection's executor."""

    def run(self, sql: str, purpose: str) -> QueryResult: ...

    def explain(self, sql: str) -> QueryEstimate: ...

    def list_tables(self) -> tuple[TableInfo, ...]: ...

    def describe_table(self, name: str) -> tuple[ColumnInfo, ...]: ...

    def distinct_values(self, table: str, column: str, limit: int = ...) -> tuple[str, ...]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SelectedTable:
    """A selected table: its schema, name, the name the executor uses and visible columns."""

    schema: str
    name: str
    display: str
    columns: tuple[str, ...]
    hides_columns: bool


def display_name(dialect: Dialect, schema: str, name: str) -> str:
    """The name the source executor lists: bare in DuckDB's ``main``, else ``schema.table``."""
    if dialect == "duckdb" and schema == DUCKDB_DEFAULT_SCHEMA:
        return name
    return f"{schema}.{name}"


def _has_expanding_star(sql: str, dialect: Dialect) -> bool:
    """Whether ``sql`` has a ``*`` outside ``count(*)`` (fails closed on a parse error)."""
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except (SqlglotError, RecursionError):
        return True
    return any(
        not isinstance(star.parent, exp.Count)
        for statement in statements
        if statement is not None
        for star in statement.find_all(exp.Star)
    )


class SelectionExecutor:
    """Runs SQL and schema lookups over the selected part of a source only; thread-safe."""

    def __init__(
        self, inner: SourceExecutor, tables: Sequence[SelectedTable], dialect: Dialect
    ) -> None:
        self._inner = inner
        self._dialect: Dialect = dialect
        self._tables = tuple(tables)
        self._snapshot = CatalogSnapshot({f"{t.schema}.{t.name}": t.columns for t in tables})
        self._restricted = frozenset(
            f"{t.schema}.{t.name}".lower() for t in tables if t.hides_columns
        )

    @property
    def tables(self) -> tuple[SelectedTable, ...]:
        return self._tables

    def close(self) -> None:
        """Close the inner executor. Idempotent."""
        self._inner.close()

    # --- QueryExecutor ------------------------------------------------------------

    def run(self, sql: str, purpose: str) -> QueryResult:
        self._check(sql)
        return self._inner.run(sql, purpose)

    def explain(self, sql: str) -> QueryEstimate:
        self._check(sql)
        return self._inner.explain(sql)

    def _check(self, sql: str) -> None:
        result = validate_sql(sql, self._dialect, catalog=self._snapshot)
        if not result.ok:
            raise QueryRejected([issue.code.value for issue in result.errors])
        touches_hidden = any(
            key in self._restricted
            for table in result.referenced_tables
            for key in self._snapshot.resolve(table)
        )
        if touches_hidden and _has_expanding_star(result.sql, self._dialect):
            raise QueryRejected([HIDDEN_COLUMNS_CODE])

    # --- schema tools ---------------------------------------------------------------

    def list_tables(self) -> tuple[TableInfo, ...]:
        """The selected tables the source still has, with the source's row counts."""
        visible = {table.display for table in self._tables}
        return tuple(info for info in self._inner.list_tables() if info.name in visible)

    def describe_table(self, name: str) -> tuple[ColumnInfo, ...]:
        table = self._resolve(name)
        allowed = set(table.columns)
        return tuple(c for c in self._inner.describe_table(table.display) if c.name in allowed)

    def distinct_values(self, table: str, column: str, limit: int = 50) -> tuple[str, ...]:
        selected = self._resolve(table)
        if column not in selected.columns:
            raise UnknownTable()
        return tuple(self._inner.distinct_values(selected.display, column, limit))

    def _resolve(self, name: str) -> SelectedTable:
        for table in self._tables:
            if name in (table.display, f"{table.schema}.{table.name}"):
                return table
        raise UnknownTable()


__all__ = [
    "HIDDEN_COLUMNS_CODE",
    "SelectedTable",
    "SelectionExecutor",
    "SourceExecutor",
    "display_name",
]
