"""Structural rules: dialect support, statement count, read-only shape, forbidden calls."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from rootcause_connections.validation.result import IssueCode, ValidationIssue

SUPPORTED_DIALECTS: Final[frozenset[str]] = frozenset(
    {"postgres", "duckdb", "mysql", "tsql", "snowflake", "bigquery", "clickhouse"}
)

#: Expression classes whose presence anywhere in the tree makes a statement non-read-only.
FORBIDDEN_EXPRESSIONS: Final[tuple[type[exp.Expr], ...]] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Revoke,
    exp.Use,
    exp.Copy,
    exp.Into,
    exp.Lock,
    exp.LoadData,
    exp.Pragma,
    exp.Kill,
    exp.Attach,
    exp.Detach,
    exp.Install,
    exp.Cache,
    exp.Uncache,
    exp.Refresh,
    exp.Declare,
)

#: Lower-cased function names that sleep, touch the file system, or control sessions.
FORBIDDEN_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "sleep",
        "benchmark",
        "load_file",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "lo_import",
        "lo_export",
        "xp_cmdshell",
        "system",
    }
)

#: Top-level expression classes accepted as a read-only query.
READ_ONLY_ROOTS: Final[tuple[type[exp.Expr], ...]] = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
)


def dialect_issue(dialect: str) -> ValidationIssue | None:
    """Return an issue when ``dialect`` is outside the supported set."""
    if dialect in SUPPORTED_DIALECTS:
        return None
    supported = ", ".join(sorted(SUPPORTED_DIALECTS))
    return ValidationIssue(
        IssueCode.UNSUPPORTED_DIALECT,
        f"Dialect {dialect!r} is not supported; expected one of: {supported}.",
    )


def parse_statements(sql: str, dialect: str) -> tuple[exp.Expr, ...] | ValidationIssue:
    """Parse ``sql`` into real statements, dropping comment-only and empty fragments.

    sqlglot's tokenizer discards comments before statements are split, so a comment
    can never hide a trailing statement; a comment left after a ``;`` surfaces as an
    empty ``Semicolon`` pseudo-statement, which is not counted.
    """
    try:
        parsed = sqlglot.parse(sql, read=dialect)
    except (ParseError, TokenError) as error:
        return ValidationIssue(IssueCode.PARSE_ERROR, _first_line(str(error)))
    return tuple(
        statement
        for statement in parsed
        if statement is not None and not isinstance(statement, exp.Semicolon)
    )


def statement_count_issue(statements: Sequence[exp.Expr]) -> ValidationIssue | None:
    """Reject anything other than exactly one statement."""
    if len(statements) == 1:
        return None
    if not statements:
        return ValidationIssue(IssueCode.PARSE_ERROR, "No SQL statement found.")
    return ValidationIssue(
        IssueCode.MULTIPLE_STATEMENTS,
        f"Expected exactly one statement, found {len(statements)}.",
    )


def read_only_issues(statement: exp.Expr) -> Iterator[ValidationIssue]:
    """Yield an issue for the top-level shape and for every forbidden node in the tree."""
    body = statement.this if isinstance(statement, exp.With) else statement
    if not isinstance(body, READ_ONLY_ROOTS):
        yield ValidationIssue(
            IssueCode.NOT_READ_ONLY,
            f"Only SELECT queries are allowed, got {type(body).__name__.upper()}.",
        )
    seen: set[str] = set()
    for node in statement.walk():
        if isinstance(node, FORBIDDEN_EXPRESSIONS) and node.key not in seen:
            seen.add(node.key)
            yield ValidationIssue(
                IssueCode.NOT_READ_ONLY,
                f"{_describe(node)} is not permitted in a read-only query.",
            )


def forbidden_function_issues(statement: exp.Expr) -> Iterator[ValidationIssue]:
    """Yield an issue for every call to a function on the deny list."""
    seen: set[str] = set()
    for node in statement.find_all(exp.Func):
        name = _function_name(node)
        if name in FORBIDDEN_FUNCTIONS and name not in seen:
            seen.add(name)
            yield ValidationIssue(
                IssueCode.FORBIDDEN_FUNCTION, f"Function {name}() is not permitted."
            )


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return node.name.lower()
    return node.sql_name().lower()


def _describe(node: exp.Expr) -> str:
    if isinstance(node, exp.Into):
        return "SELECT ... INTO"
    if isinstance(node, exp.Command):
        return f"Command {node.name.upper()}"
    return type(node).__name__.upper()


def _first_line(message: str) -> str:
    return message.splitlines()[0] if message else "Unable to parse SQL."
