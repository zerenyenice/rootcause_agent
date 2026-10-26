"""Scope-aware extraction of the tables and columns a query references.

sqlglot's scope analysis decides, per SELECT, which sources are physical tables and
which are CTEs, derived tables or table functions, so a CTE or subquery alias never
hides a physical table of the same name in another scope.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import NamedTuple

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

# DuckDB (and others) let WHERE and later projections refer to a select alias.
_CLAUSES_RESOLVING_ALIASES = (exp.Order, exp.Group, exp.Having, exp.Qualify, exp.Where)


class TableRef(NamedTuple):
    """A physical table as written in the query, with its alias when present."""

    name: str
    alias: str

    @property
    def qualifier(self) -> str:
        """The lower-cased name columns use to refer to this table."""
        return (self.alias or self.name.rsplit(".", 1)[-1]).lower()


class ColumnRef(NamedTuple):
    """A column as written in the query, with whatever qualifiers were given."""

    name: str
    qualifier: str
    db: str = ""
    catalog: str = ""
    #: a bare name that also matches a select alias usable here (WHERE, later projection,
    #: ORDER/GROUP/HAVING/QUALIFY); DuckDB binds a source column first, the alias otherwise
    alias_candidate: bool = False

    @property
    def display(self) -> str:
        return ".".join(part for part in (self.catalog, self.db, self.qualifier, self.name) if part)


class ScopeRefs(NamedTuple):
    """What one SELECT scope contributes to catalog checking."""

    tables: tuple[TableRef, ...]
    columns: tuple[ColumnRef, ...]
    #: lower-cased qualifier -> physical table, or ``None`` for derived/CTE/function sources
    qualifiers: Mapping[str, TableRef | None]
    #: output column names of derived sources whose columns are known
    derived_columns: frozenset[str]
    #: True when a source's columns cannot be determined (star, table function, ...)
    opaque: bool
    #: enclosing SELECT scopes, nearest first, for correlated-subquery column lookup
    outer: tuple[ScopeRefs, ...] = ()


class References(NamedTuple):
    """Everything a query refers to, in order of first appearance."""

    tables: tuple[TableRef, ...]
    columns: tuple[ColumnRef, ...]
    scopes: tuple[ScopeRefs, ...]


def extract_references(statement: exp.Expr) -> References:
    """Collect physical tables, columns, and per-scope checking data from ``statement``."""
    scopes = traverse_scope(statement)
    physical = {id(table): scope for scope in scopes for table in _physical_tables(scope)}
    tables = _unique(
        _table_ref(table)
        for table in statement.find_all(exp.Table, bfs=False)
        if id(table) in physical
    )
    columns = _unique(_column_ref(column) for column in statement.find_all(exp.Column, bfs=False))
    return References(tables, columns, _scope_refs(statement, scopes))


def _physical_tables(scope: Scope) -> tuple[exp.Table, ...]:
    """Tables read from storage: not CTE references, derived tables or table functions."""
    ctes = _visible_ctes(scope)
    return tuple(table for table in scope.tables if _is_physical(table, ctes))


def _visible_ctes(scope: Scope) -> frozenset[str]:
    return frozenset(name.lower() for name in scope.cte_sources)


def _is_physical(table: exp.Table, ctes: frozenset[str]) -> bool:
    if not isinstance(table.this, exp.Identifier):
        return False
    # A CTE only shadows unqualified names: ``hr.recent`` is never the CTE ``recent``.
    return bool(table.db or table.catalog) or table.name.lower() not in ctes


def _table_ref(table: exp.Table) -> TableRef:
    parts = (table.catalog, table.db, table.name)
    return TableRef(".".join(part for part in parts if part), table.alias)


def _column_ref(column: exp.Column) -> ColumnRef:
    return ColumnRef(column.name, column.table, column.db, column.catalog)


def _scope_refs(statement: exp.Expr, scopes: list[Scope]) -> tuple[ScopeRefs, ...]:
    by_expression = {id(scope.expression): index for index, scope in enumerate(scopes)}
    owned: dict[int, list[ColumnRef]] = {}
    for column, owner, alias_candidate in _owned_columns(statement, scopes, by_expression):
        column_ref = _column_ref(column)._replace(alias_candidate=alias_candidate)
        owned.setdefault(owner, []).append(column_ref)
    qualifiers: dict[int, Mapping[str, TableRef | None]] = {}
    built = {
        id(scope): _build_scope(scope, tuple(owned.get(index, ())), qualifiers)
        for index, scope in enumerate(scopes)
        if isinstance(scope.expression, exp.Select)
    }
    return tuple(
        ref._replace(outer=tuple(built[id(a)] for a in _ancestors(scope) if id(a) in built))
        for scope in scopes
        if (ref := built.get(id(scope))) is not None
    )


def _ancestors(scope: Scope) -> Iterable[Scope]:
    """Enclosing scopes of ``scope``, nearest first."""
    parent = scope.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def _owned_columns(
    statement: exp.Expr, scopes: list[Scope], by_expression: Mapping[int, int]
) -> Iterable[tuple[exp.Column, int, bool]]:
    """Pair each checkable column with its owning scope (nearest enclosing) and alias flag."""
    for column in statement.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue
        owner, in_alias_clause = _owner(column, by_expression)
        if owner is None:
            continue
        select = scopes[owner].expression
        alias_candidate = (
            not column.table
            and _is_projection_alias(select, column.name)
            and (in_alias_clause or _in_other_projection(select, column))
        )
        yield column, owner, alias_candidate


def _owner(column: exp.Column, by_expression: Mapping[int, int]) -> tuple[int | None, bool]:
    in_alias_clause = False
    node = column.parent
    while node is not None:
        if id(node) in by_expression:
            return by_expression[id(node)], in_alias_clause
        in_alias_clause = in_alias_clause or isinstance(node, _CLAUSES_RESOLVING_ALIASES)
        node = node.parent
    return None, False


def _in_other_projection(select: exp.Expr, column: exp.Column) -> bool:
    """Whether ``column`` sits in a projection other than the one aliased with its own name.

    ``SELECT a * 2 AS b, b + 1`` may read the alias ``b`` (a lateral column alias) while
    ``SELECT x AS x`` can only read the column ``x``. Whether the alias is really used is
    decided by the catalog check: a column of a source in scope wins over the alias.
    """
    name = column.name.lower()
    for projection in select.expressions:
        if projection is column or column in projection.find_all(exp.Column):
            return not (isinstance(projection, exp.Alias) and projection.alias.lower() == name)
    return False


def _is_projection_alias(select: exp.Expr, name: str) -> bool:
    lowered = name.lower()
    return any(
        isinstance(projection, exp.Alias) and projection.alias.lower() == lowered
        for projection in select.expressions
    )


def _build_scope(
    scope: Scope,
    columns: tuple[ColumnRef, ...],
    cache: dict[int, Mapping[str, TableRef | None]],
) -> ScopeRefs:
    tables = tuple(_table_ref(table) for table in _physical_tables(scope))
    derived_columns, opaque = _derived_sources(scope)
    return ScopeRefs(
        tables=tables,
        columns=_unique(columns),
        qualifiers=_qualifiers(scope, cache),
        derived_columns=derived_columns,
        opaque=opaque,
    )


def _qualifiers(
    scope: Scope, cache: dict[int, Mapping[str, TableRef | None]]
) -> Mapping[str, TableRef | None]:
    """Names usable as column qualifiers here: this scope's sources plus enclosing ones."""
    if id(scope) in cache:
        return cache[id(scope)]
    inherited = dict(_qualifiers(scope.parent, cache)) if scope.parent else {}
    local: dict[str, TableRef | None] = {name.lower(): None for name in scope.sources}
    for table in _physical_tables(scope):
        ref = _table_ref(table)
        local[ref.qualifier] = ref
    merged: Mapping[str, TableRef | None] = MappingProxyType({**inherited, **local})
    cache[id(scope)] = merged
    return merged


def _derived_sources(scope: Scope) -> tuple[frozenset[str], bool]:
    """Output columns of derived/CTE sources, and whether any source is unknowable."""
    ctes = _visible_ctes(scope)
    names: set[str] = set()
    opaque = False
    for node, source in scope.selected_sources.values():
        if isinstance(node, exp.Table) and _is_physical(node, ctes):
            continue
        known = _output_columns(node, source)
        if known is None:
            opaque = True
        else:
            names |= known
    return frozenset(names), opaque


def _output_columns(node: exp.Expr, source: exp.Table | Scope) -> frozenset[str] | None:
    if isinstance(source, Scope) and isinstance(source.expression, (exp.Select, exp.Union)):
        declared = source.outer_columns or source.expression.named_selects
    elif isinstance(node, exp.Table):
        declared = node.alias_column_names
    else:
        return None
    lowered = frozenset(name.lower() for name in declared)
    return None if not lowered or "*" in lowered else lowered


def _unique[T](items: Iterable[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(items))
