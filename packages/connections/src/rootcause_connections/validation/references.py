"""Extraction of the tables and columns a query references."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NamedTuple

from sqlglot import exp


class TableRef(NamedTuple):
    """A physical table as written in the query, with its alias when present."""

    name: str
    alias: str


class ColumnRef(NamedTuple):
    """A column as written in the query, with its qualifier (table name or alias)."""

    name: str
    qualifier: str

    @property
    def display(self) -> str:
        return f"{self.qualifier}.{self.name}" if self.qualifier else self.name


class References(NamedTuple):
    """Everything a query refers to, in order of first appearance."""

    tables: tuple[TableRef, ...]
    columns: tuple[ColumnRef, ...]
    derived_names: frozenset[str]


def extract_references(statement: exp.Expr) -> References:
    """Collect physical tables, columns, and derived (CTE/subquery) names from ``statement``."""
    derived = _derived_names(statement)
    tables = _unique(
        TableRef(_table_name(table), table.alias)
        for table in statement.find_all(exp.Table)
        if table.name.lower() not in derived
    )
    columns = _unique(
        ColumnRef(column.name, column.table) for column in statement.find_all(exp.Column)
    )
    return References(tables, columns, derived)


def alias_map(tables: Iterable[TableRef]) -> Mapping[str, str]:
    """Map lower-cased alias and table names to the table name as written."""
    mapping: dict[str, str] = {}
    for table in tables:
        mapping.setdefault(table.name.lower(), table.name)
        mapping.setdefault(table.name.rsplit(".", 1)[-1].lower(), table.name)
        if table.alias:
            mapping.setdefault(table.alias.lower(), table.name)
    return mapping


def _derived_names(statement: exp.Expr) -> frozenset[str]:
    cte_names = {cte.alias.lower() for cte in statement.find_all(exp.CTE) if cte.alias}
    subquery_aliases = {
        subquery.alias.lower() for subquery in statement.find_all(exp.Subquery) if subquery.alias
    }
    return frozenset(cte_names | subquery_aliases)


def _table_name(table: exp.Table) -> str:
    parts = (table.catalog, table.db, table.name)
    return ".".join(part for part in parts if part)


def _unique[T](items: Iterable[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(items))
