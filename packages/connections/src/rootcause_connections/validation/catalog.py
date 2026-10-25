"""Catalog snapshot used to check that referenced tables and columns exist."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NamedTuple

from rootcause_connections.validation.references import ColumnRef, References, ScopeRefs
from rootcause_connections.validation.result import IssueCode, ValidationIssue


@dataclass(frozen=True, slots=True, init=False)
class CatalogSnapshot:
    """Immutable, hashable, case-insensitive view of table names to their column names.

    Keys may be bare (``orders``) or schema-qualified (``sales.orders``). A bare query
    name resolves against every key whose last part matches; a qualified query name
    matches only the exact key, or a bare key when the snapshot has no qualified keys.
    """

    tables: tuple[tuple[str, tuple[str, ...]], ...]
    _columns: Mapping[str, frozenset[str]] = field(repr=False, compare=False)
    _by_bare: Mapping[str, tuple[str, ...]] = field(repr=False, compare=False)
    _has_qualified: bool = field(repr=False, compare=False)

    def __init__(self, tables: Mapping[str, Iterable[str]]) -> None:
        merged: dict[str, frozenset[str]] = {}
        for name, columns in tables.items():
            key = name.strip().lower()
            merged[key] = merged.get(key, frozenset()) | {c.lower() for c in columns}
        by_bare: dict[str, list[str]] = {}
        for key in sorted(merged):
            by_bare.setdefault(key.rsplit(".", 1)[-1], []).append(key)
        object.__setattr__(
            self, "tables", tuple((key, tuple(sorted(merged[key]))) for key in sorted(merged))
        )
        object.__setattr__(self, "_columns", MappingProxyType(merged))
        object.__setattr__(
            self, "_by_bare", MappingProxyType({k: tuple(v) for k, v in by_bare.items()})
        )
        object.__setattr__(self, "_has_qualified", any("." in key for key in merged))

    def resolve(self, table: str) -> tuple[str, ...]:
        """Return the catalog keys ``table`` may refer to: none, one, or several (ambiguous)."""
        name = table.lower()
        if name in self._columns:
            return (name,)
        parts = name.split(".")
        if len(parts) == 1:
            return self._by_bare.get(name, ())
        if len(parts) > 2 and (schema_table := ".".join(parts[-2:])) in self._columns:
            return (schema_table,)
        return () if self._has_qualified else self._by_bare.get(parts[-1], ())

    def columns_of(self, keys: Iterable[str]) -> frozenset[str]:
        """Union of the lower-cased columns of the given catalog keys."""
        return frozenset().union(*(self._columns.get(key, frozenset()) for key in keys))


class CatalogFindings(NamedTuple):
    """Errors (unknown tables/columns) and warnings (ambiguous names)."""

    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]


_WARNING_CODES = frozenset({IssueCode.AMBIGUOUS_COLUMN, IssueCode.AMBIGUOUS_TABLE})


def catalog_issues(catalog: CatalogSnapshot, references: References) -> CatalogFindings:
    """Check every referenced table and column against ``catalog``."""
    resolved = {table.name: catalog.resolve(table.name) for table in references.tables}
    issues = (
        *(_table_issue(name, keys) for name, keys in resolved.items()),
        *(
            _column_issue(catalog, column, scope, resolved)
            for scope in references.scopes
            for column in scope.columns
        ),
    )
    found = tuple(issue for issue in issues if issue is not None)
    return CatalogFindings(
        errors=tuple(i for i in found if i.code not in _WARNING_CODES),
        warnings=tuple(i for i in found if i.code in _WARNING_CODES),
    )


def _table_issue(name: str, keys: tuple[str, ...]) -> ValidationIssue | None:
    if not name:
        return ValidationIssue(IssueCode.UNKNOWN_TABLE, "Empty table name.")
    if not keys:
        return ValidationIssue(IssueCode.UNKNOWN_TABLE, f"Unknown table {name!r}.")
    if len(keys) > 1:
        message = f"Table {name!r} is ambiguous; it matches {', '.join(keys)}."
        return ValidationIssue(IssueCode.AMBIGUOUS_TABLE, message)
    return None


def _column_issue(
    catalog: CatalogSnapshot,
    column: ColumnRef,
    scope: ScopeRefs,
    resolved: Mapping[str, tuple[str, ...]],
) -> ValidationIssue | None:
    if column.qualifier:
        return _qualified_issue(catalog, column, scope, resolved)
    return _bare_issue(catalog, column, scope, resolved)


def _qualified_issue(
    catalog: CatalogSnapshot,
    column: ColumnRef,
    scope: ScopeRefs,
    resolved: Mapping[str, tuple[str, ...]],
) -> ValidationIssue | None:
    qualifier = column.qualifier.lower()
    if qualifier not in scope.qualifiers:
        if _is_struct_column(catalog, qualifier, scope, resolved):
            return None  # ``col.field`` on a struct column, not a table qualifier
        message = f"Unknown table or alias {column.qualifier!r}."
        return ValidationIssue(IssueCode.UNKNOWN_TABLE, message)
    table = scope.qualifiers[qualifier]
    if table is None:
        return None  # derived, CTE or function source: columns are not in the catalog
    keys = resolved.get(table.name, ())
    if not keys or column.name.lower() in catalog.columns_of(keys):
        return None  # unknown table (reported once) or a known column
    message = f"Unknown column {column.display!r} on table {table.name!r}."
    return ValidationIssue(IssueCode.UNKNOWN_COLUMN, message)


def _bare_issue(
    catalog: CatalogSnapshot,
    column: ColumnRef,
    scope: ScopeRefs,
    resolved: Mapping[str, tuple[str, ...]],
) -> ValidationIssue | None:
    name = column.name.lower()
    if name in scope.derived_columns:
        return None
    table_keys = tuple(resolved.get(table.name, ()) for table in scope.tables)
    if any(not keys for keys in table_keys):
        return None  # an unknown table makes the column unverifiable; the table is reported
    owners = tuple(
        table.name
        for table, keys in zip(scope.tables, table_keys, strict=True)
        if name in catalog.columns_of(keys)
    )
    if len(owners) > 1:
        message = f"Column {column.name!r} is ambiguous; found in {', '.join(owners)}."
        return ValidationIssue(IssueCode.AMBIGUOUS_COLUMN, message)
    if owners or scope.opaque:
        return None
    return ValidationIssue(
        IssueCode.UNKNOWN_COLUMN, f"Unknown column {column.name!r} in referenced tables."
    )


def _is_struct_column(
    catalog: CatalogSnapshot,
    qualifier: str,
    scope: ScopeRefs,
    resolved: Mapping[str, tuple[str, ...]],
) -> bool:
    return any(qualifier in catalog.columns_of(resolved.get(t.name, ())) for t in scope.tables)
