"""Read-only SQL validation gate: the single check every agent query passes before execution.

:func:`validate_sql` parses one statement with sqlglot, enforces a read-only allow list
(statement shape, a class-level allow list of syntax nodes, allow-listed functions, no
hints or file/URL sources), guarantees a capped ``LIMIT`` on the outermost query,
regenerates the SQL for the target dialect, extracts referenced tables and columns, and
optionally checks them against a :class:`CatalogSnapshot`. It is a pure function: no I/O,
inputs are never mutated.

Importing this package installs one process-wide side effect: a filter on the ``sqlglot``
logger (``logfilter.install_sqlglot_log_filter``, idempotent) that drops sqlglot's
"Falling back to parsing as a 'Command'" warning, because that record embeds raw SQL text
that must not reach logs. No other sqlglot log record is touched.
"""

from __future__ import annotations

import logging

from sqlglot import exp
from sqlglot.errors import OptimizeError

from rootcause_connections.validation.catalog import CatalogSnapshot, catalog_issues
from rootcause_connections.validation.functions import function_issues
from rootcause_connections.validation.limits import apply_limit
from rootcause_connections.validation.logfilter import install_sqlglot_log_filter
from rootcause_connections.validation.references import extract_references
from rootcause_connections.validation.result import (
    IssueCode,
    ValidationIssue,
    ValidationResult,
    failure,
)
from rootcause_connections.validation.rules import (
    SUPPORTED_DIALECTS,
    dialect_issue,
    executable_comment_issue,
    parse_statements,
    read_only_issues,
    statement_count_issue,
    statement_keyword_alias_issues,
)
from rootcause_connections.validation.sources import source_issues

__all__ = [
    "MAX_SQL_CHARS",
    "SUPPORTED_DIALECTS",
    "CatalogSnapshot",
    "IssueCode",
    "ValidationIssue",
    "ValidationResult",
    "validate_sql",
]

DEFAULT_LIMIT = 10_000
MAX_SQL_CHARS = 20_000

_logger = logging.getLogger(__name__)
install_sqlglot_log_filter()


def validate_sql(
    sql: str,
    dialect: str,
    *,
    catalog: CatalogSnapshot | None = None,
    default_limit: int = DEFAULT_LIMIT,
) -> ValidationResult:
    """Validate ``sql`` for read-only execution against ``dialect``.

    Never raises for a ``str``: oversized input, parse failures, unsupported dialects,
    and policy violations are all reported through ``errors`` on the returned result.

    Executors must run :attr:`ValidationResult.sql` (only when ``ok``) and never the
    original string: the result is regenerated from the validated syntax tree with
    comments removed and the ``LIMIT`` applied, so it is the only text that was checked.
    """
    normalised_dialect = dialect.strip().lower()
    if issue := dialect_issue(normalised_dialect):
        return failure(sql, issue)
    if len(sql) > MAX_SQL_CHARS:
        message = f"SQL is {len(sql)} characters; the maximum is {MAX_SQL_CHARS}."
        return failure(sql, ValidationIssue(IssueCode.INPUT_TOO_LARGE, message))
    try:
        return _validate(sql, normalised_dialect, catalog, default_limit)
    except OptimizeError as error:
        return failure(sql, _analysis_failure(str(error)))
    except RecursionError:
        return failure(sql, _analysis_failure("SQL is nested too deeply to analyse."))
    except Exception as error:  # the single catch-all: sqlglot may raise anything
        _logger.debug("SQL validation aborted: %s", type(error).__name__, exc_info=_redacted(error))
        return failure(sql, _analysis_failure("Unable to analyse SQL."))


def _redacted(error: Exception) -> BaseException:
    """Same traceback, message dropped: sqlglot exception messages embed SQL text."""
    return RuntimeError(type(error).__name__).with_traceback(error.__traceback__)


def _analysis_failure(message: str) -> ValidationIssue:
    return ValidationIssue(IssueCode.PARSE_ERROR, message.splitlines()[0] if message else "")


def _validate(
    sql: str,
    dialect: str,
    catalog: CatalogSnapshot | None,
    default_limit: int,
) -> ValidationResult:
    if issue := executable_comment_issue(sql, dialect):
        return failure(sql, issue)
    statements = parse_statements(sql, dialect)
    if isinstance(statements, ValidationIssue):
        return failure(sql, statements)
    if issue := statement_count_issue(statements):
        return failure(sql, issue)
    statement = statements[0]
    policy_errors = _policy_errors(statement, dialect)
    if policy_errors:
        return failure(sql, *policy_errors)
    if not isinstance(statement, exp.Query):
        return failure(sql, _not_a_query(statement))
    return _finalise(statement, dialect, catalog, default_limit)


def _policy_errors(statement: exp.Expr, dialect: str) -> tuple[ValidationIssue, ...]:
    return (
        *read_only_issues(statement),
        *statement_keyword_alias_issues(statement),
        *function_issues(statement, dialect),
        *source_issues(statement, dialect),
    )


def _not_a_query(statement: exp.Expr) -> ValidationIssue:
    """Defensive: the shape rule should already have rejected anything that is not a query."""
    return ValidationIssue(
        IssueCode.NOT_READ_ONLY,
        f"Statement of type {type(statement).__name__.upper()} is not a read-only query.",
    )


def _finalise(
    query: exp.Query,
    dialect: str,
    catalog: CatalogSnapshot | None,
    default_limit: int,
) -> ValidationResult:
    """Apply LIMIT policy, regenerate SQL, extract references, and run catalog checks."""
    references = extract_references(query)
    limited = apply_limit(query, default_limit)
    warnings: tuple[ValidationIssue, ...] = (limited.warning,) if limited.warning else ()
    errors: tuple[ValidationIssue, ...] = ()
    if catalog is not None:
        findings = catalog_issues(catalog, references)
        errors, warnings = findings.errors, warnings + findings.warnings
    return ValidationResult(
        ok=not errors,
        sql=limited.query.sql(dialect=dialect, comments=False),
        referenced_tables=tuple(dict.fromkeys(table.name for table in references.tables)),
        referenced_columns=tuple(column.display for column in references.columns),
        limit_injected=limited.injected,
        errors=errors,
        warnings=warnings,
    )
