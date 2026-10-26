"""Strict selection check: every table and column a statement reads must be selected (Sensitive).

The shared validator (:func:`rootcause.connections.validation.validate_sql`) checks
that names *exist* and is deliberately lenient where it cannot enumerate a source's
columns. This check is the opposite: it resolves every reference against a schema
holding only the selected tables and columns and fails closed on anything it cannot
resolve the way the database would.

Tables: a qualified name must be a selected ``schema.table``; a bare name must be
unique (case-insensitively) across every schema of the source, and that one
relation must be selected. CTE names shadow tables, as in the database.

Columns: sqlglot's ``qualify`` (with ``validate_qualify_columns``) resolves every
column against the selected-only schema; a hidden column cannot resolve and is
rejected. The qualified tree is then re-checked independently, because sqlglot only
knows the selected columns and so may resolve a bare name differently than the
database, which also knows the hidden ones:

* every column has a qualifier naming a source in scope, never the derived source
  that contains it (a ``LATERAL`` self-reference) and only an output column of a
  derived/``VALUES``/CTE source;
* a real-table column is a selected column of that table;
* a bare column that resolved to a derived source, or to an enclosing query, is
  rejected when a table with hidden columns sits in a nearer scope (the database
  would bind it to the hidden column first);
* a bare column that names a select alias of a scope reading a table with hidden
  columns is rejected outside ``ORDER BY`` (``WHERE``/``GROUP BY``/``HAVING`` and
  the select list bind an input column first);
* ``NATURAL`` joins and ``USING`` are rejected in statements reading a table with
  hidden columns (the database matches columns the check cannot see), and so is a
  column alias list on such a table (``t AS x(a, b)`` renames columns by position,
  hidden ones included);
* a whole-row reference is rejected: a bare name equal to a source alias reads the
  whole row (or a same-named hidden column), never a checked column. Bare names
  matching the alias of a table with hidden columns are rejected before
  qualification, and any ``TableColumn`` sqlglot produces is rejected after it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from rootcause.connections.duckdb_executor import RelationName

AMBIGUOUS_TABLE_CODE = "ambiguous_table"
UNKNOWN_TABLE_CODE = "unknown_table"
HIDDEN_COLUMN_CODE = "hidden_column"
UNRESOLVABLE_REFERENCE_CODE = "unresolvable_reference"
IMPLICIT_JOIN_COLUMNS_CODE = "implicit_join_columns"
RENAMED_COLUMNS_CODE = "renamed_columns"
ROW_REFERENCE_CODE = "row_reference"

_UNQUALIFIED = "rootcause_unqualified"
_ANY_TYPE = "TEXT"


class SelectionRejected(Exception):
    """The statement reads outside the selection; ``code`` says why (no SQL text)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StrictTable(Protocol):
    """A selected table: its schema, name, selected columns and whether some are hidden."""

    @property
    def schema(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def columns(self) -> tuple[str, ...]: ...

    @property
    def hides_columns(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    """The normalised selection and the source's relation names for one dialect."""

    dialect: str
    columns: Mapping[tuple[str, str], frozenset[str]]
    restricted: frozenset[tuple[str, str]]
    schemas_by_name: Mapping[str, tuple[str, ...]]

    @classmethod
    def build(
        cls, dialect: str, tables: Iterable[StrictTable], relations: Iterable[RelationName]
    ) -> SelectionPolicy:
        norm = _normaliser(dialect)
        selected = tuple(tables)
        schemas_by_name: dict[str, list[str]] = {}
        for relation in relations:
            schemas_by_name.setdefault(relation.name.lower(), []).append(norm(relation.schema))
        return cls(
            dialect=dialect,
            columns={
                (norm(t.schema), norm(t.name)): frozenset(norm(c) for c in t.columns)
                for t in selected
            },
            restricted=frozenset(
                (norm(t.schema), norm(t.name)) for t in selected if t.hides_columns
            ),
            schemas_by_name={k: tuple(v) for k, v in schemas_by_name.items()},
        )

    def schema(self) -> MappingSchema:
        tables: dict[str, dict[str, object]] = {}
        for (schema, table), columns in self.columns.items():
            tables.setdefault(schema, {})[table] = {c: _ANY_TYPE for c in sorted(columns)}
        mapping: dict[str, object] = dict(tables)
        return MappingSchema(mapping, dialect=self.dialect, normalize=False)


def _normaliser(dialect: str) -> Callable[[str], str]:
    """DuckDB names are case-insensitive; Postgres catalog names are exact."""
    return str.lower if dialect == "duckdb" else _identity


def _identity(name: str) -> str:
    return name


def check_selection(sql: str, policy: SelectionPolicy) -> frozenset[tuple[str, str]]:
    """Raise :class:`SelectionRejected` unless ``sql`` reads only the selection.

    Returns the (normalised) ``(schema, table)`` keys of the real tables it reads.
    """
    try:
        tree = normalize_identifiers(sqlglot.parse_one(sql, read=policy.dialect), policy.dialect)
    except Exception:  # sqlglot may raise anything; the caller validated already
        raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE) from None
    read = _resolve_tables(tree, policy)
    touches_hidden = bool(read & policy.restricted)
    if touches_hidden:
        _reject_implicit_join_columns(tree)
    _mark_unqualified(tree)
    if touches_hidden:
        _reject_alias_shadowing(tree, policy)
        _reject_restricted_alias_names(tree, policy)
    try:
        qualified = qualify(
            tree,
            dialect=policy.dialect,
            schema=policy.schema(),
            expand_stars=True,
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
    except Exception:  # unresolvable column (hidden or unknown) or unsupported shape
        raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE) from None
    _qualify_distinct_on(qualified)
    if any(True for _ in qualified.find_all(exp.TableColumn)):
        # A whole-row reference (bare source alias) is never column-checked.
        raise SelectionRejected(ROW_REFERENCE_CODE)
    for scope, columns in _columns_by_scope(qualified):
        for column in columns:
            _check_column(column, scope, policy)
    return read


def _qualify_distinct_on(tree: exp.Expr) -> None:
    """Qualify ``DISTINCT ON (col)`` names, which sqlglot's ``qualify`` leaves untouched.

    A bare name is resolved only through a projection of the same query that is already
    qualified (``SELECT DISTINCT ON (a) t.a AS a``); anything else stays bare and is
    rejected by the column check (fail closed). The ``_UNQUALIFIED`` mark is kept, so the
    hidden-column binding rules still apply to it.
    """
    for select in tree.find_all(exp.Select):
        distinct = select.args.get("distinct")
        on = distinct.args.get("on") if isinstance(distinct, exp.Distinct) else None
        if on is None:
            continue
        for column in on.find_all(exp.Column):
            if column.table:
                continue
            for projection in select.selects:
                inner = projection.this if isinstance(projection, exp.Alias) else projection
                if (
                    projection.alias_or_name == column.name
                    and isinstance(inner, exp.Column)
                    and inner.table
                ):
                    column.set("table", exp.to_identifier(inner.table))
                    break


def _scopes(tree: exp.Expr) -> Sequence[Scope]:
    try:
        return tuple(traverse_scope(tree))
    except Exception:
        raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE) from None


def _columns_by_scope(tree: exp.Expr) -> tuple[tuple[Scope, tuple[exp.Column, ...]], ...]:
    """Every column of ``tree``, each in its innermost scope.

    ``Scope.columns`` leaves out bare names in ``HAVING``/``QUALIFY`` and ``ORDER BY``
    alias references, so it cannot be the basis of an access check. Scopes are
    traversed innermost first, so the first scope containing a column owns it. A
    column outside every scope fails closed.
    """
    claimed: set[int] = set()
    grouped: list[tuple[Scope, tuple[exp.Column, ...]]] = []
    for scope in _scopes(tree):
        own = tuple(
            column for column in scope.expression.find_all(exp.Column) if id(column) not in claimed
        )
        claimed.update(id(column) for column in own)
        grouped.append((scope, own))
    if any(id(column) not in claimed for column in tree.find_all(exp.Column)):
        raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE)
    return tuple(grouped)


# --- tables -----------------------------------------------------------------------------


def _resolve_tables(tree: exp.Expr, policy: SelectionPolicy) -> frozenset[tuple[str, str]]:
    """Qualify every real table in place with its schema; reject unselected/ambiguous ones."""
    cte_refs: set[int] = set()
    for scope in _scopes(tree):
        for node, source in scope.selected_sources.values():
            if isinstance(node, exp.Table) and isinstance(source, Scope):
                cte_refs.add(id(node))
    read: set[tuple[str, str]] = set()
    for table in tuple(tree.find_all(exp.Table)):
        if id(table) in cte_refs:
            continue
        key = _table_key(table, policy)
        alias = table.args.get("alias")
        if key in policy.restricted and alias is not None and alias.columns:
            # ``t AS x(a, b, c)`` renames columns by position, hidden ones included.
            raise SelectionRejected(RENAMED_COLUMNS_CODE)
        if not table.args.get("db"):
            table.set("db", exp.to_identifier(key[0], quoted=True))
        read.add(key)
    return frozenset(read)


def _table_key(table: exp.Table, policy: SelectionPolicy) -> tuple[str, str]:
    if not isinstance(table.this, exp.Identifier) or table.args.get("catalog"):
        raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE)
    name = table.name
    if table.args.get("db"):
        key = (table.db, name)
    else:
        schemas = policy.schemas_by_name.get(name.lower(), ())
        if len(schemas) > 1:
            raise SelectionRejected(AMBIGUOUS_TABLE_CODE)
        if not schemas:
            raise SelectionRejected(UNKNOWN_TABLE_CODE)
        key = (schemas[0], name)
    if key not in policy.columns:
        raise SelectionRejected(UNKNOWN_TABLE_CODE)
    return key


def _reject_implicit_join_columns(tree: exp.Expr) -> None:
    for join in tree.find_all(exp.Join):
        if join.method.upper() == "NATURAL" or join.args.get("using"):
            raise SelectionRejected(IMPLICIT_JOIN_COLUMNS_CODE)


# --- columns ----------------------------------------------------------------------------


def _mark_unqualified(tree: exp.Expr) -> None:
    for column in tree.find_all(exp.Column):
        if not column.table:
            column.meta[_UNQUALIFIED] = True


def _reject_alias_shadowing(tree: exp.Expr, policy: SelectionPolicy) -> None:
    """A select alias named like a hidden column may bind to that column instead."""
    for scope, columns in _columns_by_scope(tree):
        if not _restricted_tables(scope, policy) or not isinstance(scope.expression, exp.Select):
            continue
        aliases = {s.alias for s in scope.expression.selects if isinstance(s, exp.Alias)}
        visible: frozenset[str] = frozenset().union(
            *(policy.columns[key] for key in _tables(scope, policy))
        )
        for column in columns:
            if column.table or _is_bare_order_key(column, scope) or column.name not in aliases:
                continue
            if column.name not in visible:
                raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE)


def _reject_restricted_alias_names(tree: exp.Expr, policy: SelectionPolicy) -> None:
    """A bare name equal to the alias of a table with hidden columns reads its whole row."""
    for scope, columns in _columns_by_scope(tree):
        aliases = _restricted_aliases(scope, policy)
        if any(not column.table and column.name in aliases for column in columns):
            raise SelectionRejected(ROW_REFERENCE_CODE)


def _restricted_aliases(scope: Scope, policy: SelectionPolicy) -> frozenset[str]:
    """Names of sources over a table with hidden columns, in ``scope`` or enclosing it."""
    names: set[str] = set()
    current: Scope | None = scope
    while current is not None:
        sources = (
            *((name, source) for name, (_, source) in current.selected_sources.items()),
            *current.lateral_sources.items(),
        )
        names.update(
            name
            for name, source in sources
            if isinstance(source, exp.Table) and (source.db, source.name) in policy.restricted
        )
        current = current.parent
    return frozenset(names)


def _is_bare_order_key(column: exp.Column, scope: Scope) -> bool:
    """Whether ``column`` is a whole ``ORDER BY`` key of its own query (not a window's).

    Only there does a simple name bind to an output alias before an input column; inside
    an expression (``ORDER BY lower(x)``) or a window it binds to the input column.
    """
    ordered = column.parent
    order = ordered.parent if isinstance(ordered, exp.Ordered) else None
    return isinstance(order, exp.Order) and order.parent is scope.expression


def _check_column(column: exp.Column, scope: Scope, policy: SelectionPolicy) -> None:
    if not column.table:
        if _is_order_by_alias(column, scope):
            return
        raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE)
    found = _lookup(column.table, scope)
    if found is None:
        raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE)
    owner, source = found
    if isinstance(source, exp.Table):
        if column.name not in policy.columns.get((source.db, source.name), frozenset()):
            raise SelectionRejected(HIDDEN_COLUMN_CODE)
        nearer = _path(scope, owner)[:-1]
    else:
        if _contains(source, scope) or column.name not in _output_names(source):
            raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE)
        nearer = _path(scope, owner)
    if column.meta.get(_UNQUALIFIED) and any(_restricted_tables(s, policy) for s in nearer):
        raise SelectionRejected(UNRESOLVABLE_REFERENCE_CODE)


def _is_order_by_alias(column: exp.Column, scope: Scope) -> bool:
    query = scope.expression
    return (
        isinstance(query, exp.Query)
        and _is_bare_order_key(column, scope)
        and column.name in query.named_selects
    )


def _lookup(name: str, scope: Scope) -> tuple[Scope, exp.Table | Scope] | None:
    """The scope declaring source ``name`` (innermost first) and the source itself."""
    current: Scope | None = scope
    while current is not None:
        if name in current.selected_sources:
            return current, current.selected_sources[name][1]
        if name in current.lateral_sources:
            return current, current.lateral_sources[name]
        current = current.parent
    return None


def _path(scope: Scope, owner: Scope) -> tuple[Scope, ...]:
    """Scopes from ``scope`` out to ``owner`` (inclusive)."""
    path: list[Scope] = []
    current: Scope | None = scope
    while current is not None:
        path.append(current)
        if current is owner:
            break
        current = current.parent
    return tuple(path)


def _contains(source: Scope, scope: Scope) -> bool:
    current: Scope | None = scope
    while current is not None:
        if current is source:
            return True
        current = current.parent
    return False


def _output_names(source: Scope) -> frozenset[str]:
    node = source.expression
    if isinstance(node, exp.Lateral | exp.Values | exp.Subquery) and node.alias_column_names:
        return frozenset(node.alias_column_names)
    if isinstance(node, exp.Lateral):
        node = node.this
    if isinstance(node, exp.Query | exp.Subquery):
        return frozenset(name for name in node.named_selects if name != "*")
    return frozenset()


def _tables(scope: Scope, policy: SelectionPolicy) -> tuple[tuple[str, str], ...]:
    sources = (
        *(source for _, source in scope.selected_sources.values()),
        *scope.lateral_sources.values(),
    )
    return tuple(
        (s.db, s.name)
        for s in sources
        if isinstance(s, exp.Table) and (s.db, s.name) in policy.columns
    )


def _restricted_tables(scope: Scope, policy: SelectionPolicy) -> bool:
    return any(key in policy.restricted for key in _tables(scope, policy))


__all__ = [
    "AMBIGUOUS_TABLE_CODE",
    "HIDDEN_COLUMN_CODE",
    "IMPLICIT_JOIN_COLUMNS_CODE",
    "RENAMED_COLUMNS_CODE",
    "ROW_REFERENCE_CODE",
    "UNKNOWN_TABLE_CODE",
    "UNRESOLVABLE_REFERENCE_CODE",
    "RelationName",
    "SelectionPolicy",
    "SelectionRejected",
    "StrictTable",
    "check_selection",
]
