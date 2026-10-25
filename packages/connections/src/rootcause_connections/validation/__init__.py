"""Read-only SQL validation gate: the single check every agent query passes before execution.

:func:`validate_sql` parses one statement with sqlglot, enforces a read-only allow list,
injects or caps ``LIMIT`` on the outermost query, transpiles to the target dialect,
extracts referenced tables and columns, and optionally checks them against a
:class:`CatalogSnapshot`. It is a pure function: no I/O, inputs are never mutated.
"""

from __future__ import annotations

from sqlglot import exp

from rootcause_connections.validation.catalog import CatalogSnapshot, catalog_issues
from rootcause_connections.validation.limits import apply_limit
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
    forbidden_function_issues,
    parse_statements,
    read_only_issues,
    statement_count_issue,
)

__all__ = [
    "SUPPORTED_DIALECTS",
    "CatalogSnapshot",
    "IssueCode",
    "ValidationIssue",
    "ValidationResult",
    "validate_sql",
]

DEFAULT_LIMIT = 10_000


def validate_sql(
    sql: str,
    dialect: str,
    *,
    catalog: CatalogSnapshot | None = None,
    default_limit: int = DEFAULT_LIMIT,
) -> ValidationResult:
    """Validate ``sql`` for read-only execution against ``dialect``.

    Never raises for bad input: parse failures, unsupported dialects, and policy
    violations are all reported through ``errors`` on the returned result.
    """
    normalised_dialect = dialect.strip().lower()
    if issue := dialect_issue(normalised_dialect):
        return failure(sql, issue)
    statements = parse_statements(sql, normalised_dialect)
    if isinstance(statements, ValidationIssue):
        return failure(sql, statements)
    if issue := statement_count_issue(statements):
        return failure(sql, issue)
    statement = statements[0]
    policy_errors = (*read_only_issues(statement), *forbidden_function_issues(statement))
    if policy_errors:
        return failure(sql, *policy_errors)
    if not isinstance(statement, exp.Query):
        return failure(sql, _not_a_query(statement))
    return _finalise(statement, normalised_dialect, catalog, default_limit)


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
    """Apply LIMIT policy, transpile, extract references, and run catalog checks."""
    references = extract_references(query)
    limited = apply_limit(query, default_limit)
    warnings: tuple[ValidationIssue, ...] = (limited.warning,) if limited.warning else ()
    errors: tuple[ValidationIssue, ...] = ()
    if catalog is not None:
        findings = catalog_issues(catalog, references)
        errors, warnings = findings.errors, warnings + findings.warnings
    return ValidationResult(
        ok=not errors,
        sql=limited.query.sql(dialect=dialect),
        referenced_tables=tuple(table.name for table in references.tables),
        referenced_columns=tuple(column.display for column in references.columns),
        limit_injected=limited.injected,
        errors=errors,
        warnings=warnings,
    )
