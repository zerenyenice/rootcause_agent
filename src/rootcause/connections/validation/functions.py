"""Function allow-list enforcement and table-source rules.

Every function call must be an allow-listed typed sqlglot class (see ``allowlist``), or
an unknown (``exp.Anonymous``) name explicitly listed for the dialect. Anything else fails
closed with ``FORBIDDEN_FUNCTION``. DuckDB is the exception: its connection is locked
down, so it allows every function except an explicit deny list (files, system state,
nested SQL, row generators). Table-position calls (``FROM f(...)``,
``LATERAL f(...)``, ``TABLE(f(...))``) are limited to the generate_series /
unnest families. Table *names* are judged by ``sources``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from sqlglot import exp

from rootcause.connections.validation.allowlist import (
    ALLOWED_ANONYMOUS,
    ALLOWED_TYPE_SET,
    DEFAULT_TABLE_FUNCTION_NAMES,
    DUCKDB_TYPE_SET,
    TABLE_FUNCTION_NAMES,
    TABLE_FUNCTION_TYPES,
    duckdb_function_name_allowed,
)
from rootcause.connections.validation.result import IssueCode, ValidationIssue

_GENERATED_CALL: Final = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")


@dataclass(frozen=True, slots=True)
class FunctionCatalog:
    """Function names of a DuckDB database: its built-ins and the user-defined ones.

    A macro stored in the customer's file expands at bind time (it can read any table)
    and can shadow a built-in name (``round``), so DuckDB calls are judged against this
    catalog: a name defined by the user is never allowed, and a name unknown to sqlglot
    must be a built-in. Without a catalog DuckDB falls back to the strict allow list.
    """

    builtin: frozenset[str]
    user: frozenset[str]


_ANONYMOUS_TYPES: Final[tuple[type[exp.Expr], ...]] = (exp.Anonymous, exp.AnonymousAggFunc)


def function_issues(
    statement: exp.Expr, dialect: str, functions: FunctionCatalog | None = None
) -> Iterator[ValidationIssue]:
    """Yield ``FORBIDDEN_FUNCTION`` for every call that is not allow-listed, once per name."""
    table_calls = {id(call) for call in _table_position_calls(statement)}
    seen: set[str] = set()
    for node in statement.find_all(exp.Func):
        in_table = id(node) in table_calls
        allowed = (
            _table_call_allowed(node, dialect)
            if in_table
            else _call_allowed(node, dialect, functions)
        )
        if not allowed:
            yield from _once(seen, _forbidden(_function_name(node), in_table))
    for dot in statement.find_all(exp.Dot):
        if isinstance(dot.expression, exp.Func):
            name = f"{dot.this.sql()}.{_function_name(dot.expression)}"
            yield from _once(seen, _forbidden(name, False, qualified=True))


def _call_allowed(node: exp.Func, dialect: str, functions: FunctionCatalog | None) -> bool:
    if dialect != "duckdb":
        if isinstance(node, _ANONYMOUS_TYPES):
            if node.args.get("db") or node.args.get("catalog"):
                return False
            return node.name.lower() in ALLOWED_ANONYMOUS.get(dialect, frozenset())
        return type(node) in ALLOWED_TYPE_SET
    return _duckdb_call_allowed(node, functions)


def _duckdb_call_allowed(node: exp.Func, functions: FunctionCatalog | None) -> bool:
    if isinstance(node, _ANONYMOUS_TYPES):
        if node.args.get("db") or node.args.get("catalog"):
            return False
        name = node.name.lower()
        if functions is not None and name in functions.user:
            return False
        if name in ALLOWED_ANONYMOUS["duckdb"]:
            return True
        return (
            functions is not None
            and name in functions.builtin
            and duckdb_function_name_allowed(name)
        )
    if functions is None:
        return type(node) in ALLOWED_TYPE_SET
    return type(node) in DUCKDB_TYPE_SET and _generated_name(node) not in functions.user


def _generated_name(node: exp.Func) -> str:
    """The function name DuckDB will bind, as written in the regenerated SQL."""
    match = _GENERATED_CALL.match(node.sql(dialect="duckdb"))
    return match.group(1).lower() if match else ""


def _table_call_allowed(node: exp.Func, dialect: str) -> bool:
    if isinstance(node, _ANONYMOUS_TYPES):
        names = TABLE_FUNCTION_NAMES.get(dialect, DEFAULT_TABLE_FUNCTION_NAMES)
        return node.name.lower() in names
    return type(node) in TABLE_FUNCTION_TYPES


def _table_position_calls(statement: exp.Expr) -> Iterator[exp.Func]:
    for node in statement.find_all(exp.Table, exp.Lateral, exp.TableFromRows):
        if isinstance(node.this, exp.Func):
            yield node.this


def _function_name(node: exp.Func) -> str:
    if isinstance(node, _ANONYMOUS_TYPES):
        return node.name.lower()
    return node.sql_name().lower()


def _forbidden(name: str, in_table: bool, *, qualified: bool = False) -> ValidationIssue:
    if qualified:
        detail = f"Qualified function call {name}() is not permitted."
    elif in_table:
        detail = f"Table function {name}() is not permitted."
    else:
        detail = f"Function {name}() is not allowed; use standard SQL functions instead."
    return ValidationIssue(IssueCode.FORBIDDEN_FUNCTION, detail)


def _once(seen: set[str], issue: ValidationIssue) -> Iterator[ValidationIssue]:
    if issue.message not in seen:
        seen.add(issue.message)
        yield issue
