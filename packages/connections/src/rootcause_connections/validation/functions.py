"""Function allow-list enforcement and table-source rules.

Every function call must be an allow-listed typed sqlglot class (see ``allowlist``), or
an unknown (``exp.Anonymous``) name explicitly listed for the dialect. Anything else fails
closed with ``FORBIDDEN_FUNCTION``. Table-position calls (``FROM f(...)``,
``LATERAL f(...)``, ``TABLE(f(...))``) are limited to the generate_series /
unnest families, and file, URL or stage style table names are rejected.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from sqlglot import exp

from rootcause_connections.validation.allowlist import (
    ALLOWED_ANONYMOUS,
    ALLOWED_TYPE_SET,
    DEFAULT_TABLE_FUNCTION_NAMES,
    TABLE_FUNCTION_NAMES,
    TABLE_FUNCTION_TYPES,
)
from rootcause_connections.validation.result import IssueCode, ValidationIssue

_PATH_SUFFIXES: Final[tuple[str, ...]] = (".csv", ".parquet", ".json", ".jsonl", ".tsv")
_ANONYMOUS_TYPES: Final[tuple[type[exp.Expr], ...]] = (exp.Anonymous, exp.AnonymousAggFunc)


def function_issues(statement: exp.Expr, dialect: str) -> Iterator[ValidationIssue]:
    """Yield ``FORBIDDEN_FUNCTION`` for every call that is not allow-listed, once per name."""
    table_calls = {id(call) for call in _table_position_calls(statement)}
    seen: set[str] = set()
    for node in statement.find_all(exp.Func):
        in_table = id(node) in table_calls
        allowed = _table_call_allowed(node, dialect) if in_table else _call_allowed(node, dialect)
        if not allowed:
            yield from _once(seen, _forbidden(_function_name(node), in_table))
    for dot in statement.find_all(exp.Dot):
        if isinstance(dot.expression, exp.Func):
            name = f"{dot.this.sql()}.{_function_name(dot.expression)}"
            yield from _once(seen, _forbidden(name, False, qualified=True))


def source_issues(statement: exp.Expr) -> Iterator[ValidationIssue]:
    """Yield ``NOT_READ_ONLY`` for table names that are files, URLs, stages or placeholders."""
    for table in statement.find_all(exp.Table):
        source = table.this
        if isinstance(source, (exp.Func, exp.Dot)):
            continue  # calls are judged by function_issues
        if not isinstance(source, exp.Identifier):
            yield _not_read_only("Table source is not a plain table name.")
        elif any(_looks_like_path(part) for part in (table.catalog, table.db, table.name)):
            yield _not_read_only(
                f"Table name {table.name!r} looks like a file, URL or stage reference."
            )


def _call_allowed(node: exp.Func, dialect: str) -> bool:
    if isinstance(node, _ANONYMOUS_TYPES):
        if node.args.get("db") or node.args.get("catalog"):
            return False
        return node.name.lower() in ALLOWED_ANONYMOUS.get(dialect, frozenset())
    return type(node) in ALLOWED_TYPE_SET


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
        detail = f"Function {name}() is not permitted."
    return ValidationIssue(IssueCode.FORBIDDEN_FUNCTION, detail)


def _not_read_only(message: str) -> ValidationIssue:
    return ValidationIssue(IssueCode.NOT_READ_ONLY, message)


def _once(seen: set[str], issue: ValidationIssue) -> Iterator[ValidationIssue]:
    if issue.message not in seen:
        seen.add(issue.message)
        yield issue


def _looks_like_path(part: str) -> bool:
    lowered = part.lower()
    return (
        "/" in lowered
        or "\\" in lowered
        or lowered.startswith("@")
        or lowered.endswith(_PATH_SUFFIXES)
    )
