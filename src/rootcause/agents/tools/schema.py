"""``list_tables`` / ``describe_table`` / ``column_values`` over a schema-capable executor.

Tools depend only on the :class:`SchemaExecutor` protocol. Table and column
names are checked against the warehouse catalog by the executor; the tools add
the human-written descriptions from ``table_docs`` when present. Failures come
back as ``{"error": <generic message>}`` and are logged by exception type only,
because database errors can quote names and values.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from langchain_core.tools import BaseTool, tool

from rootcause.logging import get_logger

_logger = get_logger(__name__)
COLUMN_VALUES_LIMIT = 50
_UNKNOWN_MESSAGE = (
    "Unknown table or column. Call list_tables and describe_table to see valid names."
)
_FAILURE_MESSAGE = "The schema lookup could not be completed."
# Matched by class name so the tools do not import a concrete executor module.
_UNKNOWN_ERROR_TYPES = frozenset({"UnknownTable"})


class TableInfoLike(Protocol):
    """A table name with its row count."""

    @property
    def name(self) -> str: ...

    @property
    def row_count(self) -> int: ...


class ColumnInfoLike(Protocol):
    """A column name with its type."""

    @property
    def name(self) -> str: ...

    @property
    def data_type(self) -> str: ...


class TableDocLike(Protocol):
    """Documentation for one table and its columns."""

    @property
    def description(self) -> str: ...

    @property
    def columns(self) -> Mapping[str, str]: ...


@runtime_checkable
class SchemaExecutor(Protocol):
    """Catalog introspection the schema tools depend on."""

    def list_tables(self) -> Sequence[TableInfoLike]: ...

    def describe_table(self, name: str) -> Sequence[ColumnInfoLike]: ...

    def distinct_values(
        self, table: str, column: str, limit: int = COLUMN_VALUES_LIMIT
    ) -> Sequence[str]: ...


def _error(exc: Exception, tool_name: str) -> dict[str, Any]:
    """Log only the failure's type and return a value-free payload."""
    error_type = type(exc).__name__
    _logger.warning("schema_tool_failed", tool=tool_name, error_type=error_type)
    unknown = error_type in _UNKNOWN_ERROR_TYPES
    return {"error": _UNKNOWN_MESSAGE if unknown else _FAILURE_MESSAGE}


def make_schema_tools(
    executor: SchemaExecutor,
    table_docs: Mapping[str, TableDocLike] | None = None,
) -> list[BaseTool]:
    """Build the three schema tools bound to ``executor`` and optional ``table_docs``."""
    docs: Mapping[str, TableDocLike] = table_docs if table_docs is not None else {}

    def table_description(table: str) -> str:
        doc = docs.get(table)
        return doc.description if doc is not None else ""

    def column_description(table: str, column: str) -> str:
        doc = docs.get(table)
        return doc.columns.get(column, "") if doc is not None else ""

    @tool
    def list_tables() -> dict[str, Any]:
        """List the warehouse tables with their row counts and descriptions."""
        try:
            tables = executor.list_tables()
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as a payload
            return _error(exc, "list_tables")
        return {
            "tables": [
                {"name": t.name, "row_count": t.row_count, "description": table_description(t.name)}
                for t in tables
            ]
        }

    @tool
    def describe_table(table: str) -> dict[str, Any]:
        """Describe a table's columns: name, type and description."""
        try:
            columns = executor.describe_table(table)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as a payload
            return _error(exc, "describe_table")
        return {
            "table": table,
            "description": table_description(table),
            "columns": [
                {
                    "name": c.name,
                    "type": c.data_type,
                    "description": column_description(table, c.name),
                }
                for c in columns
            ],
        }

    @tool
    def column_values(table: str, column: str) -> dict[str, Any]:
        """Return up to 50 distinct values of a column, as strings."""
        try:
            values = executor.distinct_values(table, column, COLUMN_VALUES_LIMIT)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as a payload
            return _error(exc, "column_values")
        return {"table": table, "column": column, "values": list(values)}

    return [list_tables, describe_table, column_values]
