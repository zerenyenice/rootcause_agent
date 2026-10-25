"""Structural rules: dialect support, statement count, read-only shape, hints, comments."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokenizer_core import Token, TokenType

from rootcause_connections.validation.nodes import node_description
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
        "reconfigure",
        "checkpoint",
        "use",
        "print",
        "raiserror",
        "deny",
        "bulk",
        "setuser",
        "openquery",
        "openrowset",
        "revert",
        "throw",
        "goto",
        "begin",
        "commit",
        "rollback",
        "save",
        "close",
        "open",
        "deallocate",
        "fetch",
    }
)

#: Comment openers that dialects execute (MySQL ``/*! */``, MariaDB ``/*M! */``) or treat
#: as hints (``/*+ */``).
EXECUTABLE_COMMENT_OPENERS: Final[tuple[str, ...]] = ("/*!", "/*+", "/*M!")

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


def executable_comment_issue(sql: str, dialect: str) -> ValidationIssue | None:
    """Reject executable comments and optimizer hints before sqlglot discards them.

    The text between consecutive tokens is only whitespace and comments (string literals,
    quoted names and dollar-quoted bodies are tokens), so searching those gaps finds real
    comments and never ``'%/*+%'`` inside a literal. Dialect HINT tokens are rejected too.
    A tokenizer failure is reported as ``PARSE_ERROR`` (fail closed); any other exception
    propagates to the single catch-all in ``validate_sql``.
    """
    try:
        tokens = Dialect.get_or_raise(dialect).tokenize(sql)
    except TokenError as error:
        return ValidationIssue(IssueCode.PARSE_ERROR, _first_line(str(error)))
    except RecursionError:
        return ValidationIssue(IssueCode.PARSE_ERROR, "SQL is nested too deeply to parse.")
    if any(token.token_type is TokenType.HINT for token in tokens) or any(
        opener in gap for gap in _comment_gaps(sql, tokens) for opener in EXECUTABLE_COMMENT_OPENERS
    ):
        return ValidationIssue(
            IssueCode.NOT_READ_ONLY,
            "Executable comments and optimizer hints (/*! ... */, /*+ ... */, /*M! ... */) "
            "are not permitted.",
        )
    return None


def _comment_gaps(sql: str, tokens: Sequence[Token]) -> Iterator[str]:
    """Yield the raw text before each token and after the last one."""
    previous_end = -1
    for token in tokens:
        yield sql[previous_end + 1 : token.start]
        previous_end = token.end
    yield sql[previous_end + 1 :]


def parse_statements(sql: str, dialect: str) -> tuple[exp.Expr, ...] | ValidationIssue:
    """Parse ``sql`` into real statements, dropping comment-only and empty fragments.

    sqlglot's tokenizer discards comments before statements are split, so a comment
    can never hide a trailing statement; a comment left after a ``;`` surfaces as an
    empty ``Semicolon`` pseudo-statement, which is not counted. Every failure mode of
    the parser's own errors, including pathological nesting, are reported as
    ``PARSE_ERROR``; anything else propagates to the catch-all in ``validate_sql``.
    """
    try:
        parsed = sqlglot.parse(sql, read=dialect)
    except (ParseError, TokenError) as error:
        return ValidationIssue(IssueCode.PARSE_ERROR, _first_line(str(error)))
    except RecursionError:
        return ValidationIssue(IssueCode.PARSE_ERROR, "SQL is nested too deeply to parse.")
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
        description = _forbidden_description(node) or node_description(node)
        if description is not None and description not in seen:
            seen.add(description)
            yield ValidationIssue(
                IssueCode.NOT_READ_ONLY, f"{description} is not permitted in a read-only query."
            )


def statement_keyword_alias_issues(statement: exp.Expr, dialect: str) -> Iterator[ValidationIssue]:
    """Flag unquoted aliases that look like a second statement whose separator is missing.

    Only T-SQL lets a statement keyword follow an expression and start a new statement;
    in every other dialect such a word is an ordinary alias and is accepted.
    """
    if dialect != "tsql":
        return
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
    if isinstance(node, (exp.Limit, exp.Offset)) and node.expressions:
        return "LIMIT ... BY clause"
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
