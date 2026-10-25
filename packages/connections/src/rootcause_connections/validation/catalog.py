"""Catalog snapshot used to check that referenced tables and columns exist."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NamedTuple

from rootcause_connections.validation.references import ColumnRef, References, alias_map
from rootcause_connections.validation.result import IssueCode, ValidationIssue


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Immutable, case-insensitive view of table names to their column names.

    Keys may be bare (``orders``) or schema-qualified (``public.orders``); lookups
    accept either form and fall back to matching on the bare table name.
    """

    tables: Mapping[str, frozenset[str]] = field(init=False)
    _by_bare_name: Mapping[str, frozenset[str]] = field(init=False, repr=False)

    def __init__(self, tables: Mapping[str, Iterable[str]]) -> None:
        normalised = {
            name.lower(): frozenset(column.lower() for column in columns)
            for name, columns in tables.items()
        }
        by_bare: dict[str, frozenset[str]] = {}
        for name, columns in normalised.items():
            bare = name.rsplit(".", 1)[-1]
            by_bare[bare] = by_bare.get(bare, frozenset()) | columns
        object.__setattr__(self, "tables", MappingProxyType(normalised))
        object.__setattr__(self, "_by_bare_name", MappingProxyType(by_bare))

    def columns_of(self, table: str) -> frozenset[str] | None:
        """Return the lower-cased columns of ``table`` or ``None`` when it is unknown."""
        key = table.lower()
        exact = self.tables.get(key)
        if exact is not None:
            return exact
        return self._by_bare_name.get(key.rsplit(".", 1)[-1])

    def has_table(self, table: str) -> bool:
        return self.columns_of(table) is not None

    def has_column(self, table: str, column: str) -> bool:
        return column.lower() in (self.columns_of(table) or frozenset())


class CatalogFindings(NamedTuple):
    """Errors (unknown tables/columns) and warnings (ambiguous bare columns)."""

    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]


def catalog_issues(catalog: CatalogSnapshot, references: References) -> CatalogFindings:
    """Check every referenced table and column against ``catalog``."""
    table_errors = tuple(
        ValidationIssue(IssueCode.UNKNOWN_TABLE, f"Unknown table {table.name!r}.")
        for table in references.tables
        if not catalog.has_table(table.name)
    )
    known = tuple(table for table in references.tables if catalog.has_table(table.name))
    aliases = alias_map(known)
    findings = tuple(
        _column_findings(catalog, column, aliases, references.derived_names)
        for column in references.columns
    )
    errors = tuple(issue for issue, is_warning in findings if issue and not is_warning)
    warnings = tuple(issue for issue, is_warning in findings if issue and is_warning)
    return CatalogFindings(table_errors + errors, warnings)


def _column_findings(
    catalog: CatalogSnapshot,
    column: ColumnRef,
    aliases: Mapping[str, str],
    derived: frozenset[str],
) -> tuple[ValidationIssue | None, bool]:
    """Return ``(issue, is_warning)`` for one column; ``(None, False)`` when it resolves."""
    if column.qualifier:
        return _qualified_issue(catalog, column, aliases, derived), False
    owners = tuple(
        table for table in dict.fromkeys(aliases.values()) if catalog.has_column(table, column.name)
    )
    if len(owners) > 1:
        message = f"Column {column.name!r} is ambiguous; found in {', '.join(owners)}."
        return ValidationIssue(IssueCode.UNKNOWN_COLUMN, message), True
    if owners or derived:
        return None, False
    message = f"Unknown column {column.name!r} in referenced tables."
    return ValidationIssue(IssueCode.UNKNOWN_COLUMN, message), False


def _qualified_issue(
    catalog: CatalogSnapshot,
    column: ColumnRef,
    aliases: Mapping[str, str],
    derived: frozenset[str],
) -> ValidationIssue | None:
    qualifier = column.qualifier.lower()
    if qualifier in derived:
        return None
    table = aliases.get(qualifier)
    if table is None or catalog.has_column(table, column.name):
        return None
    message = f"Unknown column {column.display!r} on table {table!r}."
    return ValidationIssue(IssueCode.UNKNOWN_COLUMN, message)
