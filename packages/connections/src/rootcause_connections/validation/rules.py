"""Structural rules: dialect support, statement count, read-only shape, hints, comments."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from rootcause_connections.validation.result import IssueCode, ValidationIssue

_logger = logging.getLogger(__name__)

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
    exp.Hint,
    exp.QueryOption,
    exp.WithTableHint,
    exp.ObjectIdentifier,
    exp.UnicodeString,
)

#: Unquoted aliases that are really the start of a second statement with its separator
#: missing (T-SQL accepts ``SELECT 1 DELETE FROM t`` as two statements).
STATEMENT_KEYWORD_ALIASES: Final[frozenset[str]] = frozenset(
    {
        "delete",
        "drop",
        "insert",
        "update",
        "truncate",
        "alter",
        "create",
        "merge",
        "exec",
        "execute",
        "grant",
        "revoke",
        "shutdown",
        "waitfor",
        "kill",
        "backup",
        "restore",
        "dbcc",
        "declare",
    }
)

#: Comment openers that dialects execute (MySQL ``/*! */``) or treat as hints (``/*+ */``).
EXECUTABLE_COMMENT_OPENERS: Final[tuple[str, ...]] = ("/*!", "/*+")

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


class _CommandFallbackFilter(logging.Filter):
    """Drop sqlglot's Command-fallback warning, which embeds raw SQL text."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "Falling back to parsing as a 'Command'" not in record.getMessage()


logging.getLogger("sqlglot").addFilter(_CommandFallbackFilter())


def executable_comment_issue(sql: str) -> ValidationIssue | None:
    """Reject executable comments and optimizer hints before sqlglot discards them."""
    if any(opener in sql for opener in EXECUTABLE_COMMENT_OPENERS):
        return ValidationIssue(
            IssueCode.NOT_READ_ONLY,
            "Executable comments and optimizer hints (/*! ... */, /*+ ... */) are not permitted.",
        )
    return None


def parse_statements(sql: str, dialect: str) -> tuple[exp.Expr, ...] | ValidationIssue:
    """Parse ``sql`` into real statements, dropping comment-only and empty fragments.

    sqlglot's tokenizer discards comments before statements are split, so a comment
    can never hide a trailing statement; a comment left after a ``;`` surfaces as an
    empty ``Semicolon`` pseudo-statement, which is not counted. Every failure mode of
    the parser, including pathological nesting, is reported as ``PARSE_ERROR``.
    """
    try:
        parsed = sqlglot.parse(sql, read=dialect)
    except (ParseError, TokenError) as error:
        return ValidationIssue(IssueCode.PARSE_ERROR, _first_line(str(error)))
    except RecursionError:
        return ValidationIssue(IssueCode.PARSE_ERROR, "SQL is nested too deeply to parse.")
    except Exception as error:  # sqlglot may raise anything on hostile input
        _logger.warning("sqlglot failed to parse input: %s", type(error).__name__)
        return ValidationIssue(IssueCode.PARSE_ERROR, "Unable to parse SQL.")
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
    if not isinstance(statement, READ_ONLY_ROOTS):
        yield ValidationIssue(
            IssueCode.NOT_READ_ONLY,
            f"Only SELECT queries are allowed, got {type(statement).__name__.upper()}.",
        )
    seen: set[str] = set()
    for node in statement.walk():
        description = _forbidden_description(node)
        if description is not None and description not in seen:
            seen.add(description)
            yield ValidationIssue(
                IssueCode.NOT_READ_ONLY, f"{description} is not permitted in a read-only query."
            )


def statement_keyword_alias_issues(statement: exp.Expr) -> Iterator[ValidationIssue]:
    """Flag unquoted aliases that look like a second statement whose separator is missing."""
    seen: set[str] = set()
    for node in statement.find_all(exp.Alias, exp.TableAlias):
        alias = node.args.get("alias") if isinstance(node, exp.Alias) else node.this
        if not isinstance(alias, exp.Identifier) or alias.quoted:
            continue
        name = alias.name.lower()
        if name in STATEMENT_KEYWORD_ALIASES and name not in seen:
            seen.add(name)
            yield ValidationIssue(
                IssueCode.MULTIPLE_STATEMENTS,
                f"Alias {alias.name!r} looks like a second statement with a missing "
                "separator; quote it if it is meant as a name.",
            )


def _forbidden_description(node: exp.Expr) -> str | None:
    if isinstance(node, FORBIDDEN_EXPRESSIONS):
        return _describe(node)
    if isinstance(node, exp.PropertyEQ) and isinstance(node.this, exp.Parameter):
        return "User-variable assignment"
    if isinstance(node, exp.Select):
        return _select_clause_description(node)
    return None


def _select_clause_description(select: exp.Select) -> str | None:
    if select.args.get("settings"):
        return "SETTINGS clause"
    if select.args.get("format"):
        return "FORMAT clause"
    if any(_is_variable_assignment(projection) for projection in select.expressions):
        return "User-variable assignment"
    return None


def _is_variable_assignment(projection: exp.Expr) -> bool:
    """T-SQL ``SELECT @v = expr`` assigns instead of comparing."""
    return isinstance(projection, exp.EQ) and isinstance(projection.this, exp.Parameter)


def _describe(node: exp.Expr) -> str:
    if isinstance(node, exp.Into):
        return "SELECT ... INTO"
    if isinstance(node, exp.Command):
        return f"Command {node.name.upper()}"
    if isinstance(node, exp.Hint):
        return "Optimizer hint"
    if isinstance(node, exp.UnicodeString):
        return "Unicode-escape string literal (U&'...')"
    return type(node).__name__.upper()


def _first_line(message: str) -> str:
    return message.splitlines()[0] if message else "Unable to parse SQL."
