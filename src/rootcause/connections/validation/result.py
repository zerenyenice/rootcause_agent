"""Immutable result types returned by :func:`rootcause.connections.validation.validate_sql`."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, unique


@unique
class IssueCode(Enum):
    """Machine-readable classification of a validation finding."""

    MULTIPLE_STATEMENTS = "multiple_statements"
    NOT_READ_ONLY = "not_read_only"
    FORBIDDEN_FUNCTION = "forbidden_function"
    PARSE_ERROR = "parse_error"
    UNKNOWN_TABLE = "unknown_table"
    UNKNOWN_COLUMN = "unknown_column"
    UNSUPPORTED_DIALECT = "unsupported_dialect"
    LIMIT_CAPPED = "limit_capped"
    AMBIGUOUS_COLUMN = "ambiguous_column"
    AMBIGUOUS_TABLE = "ambiguous_table"
    INPUT_TOO_LARGE = "input_too_large"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single error or warning produced while validating a statement."""

    code: IssueCode
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of validating one SQL statement.

    ``sql`` holds the normalised statement (transpiled to the requested dialect,
    comments stripped, LIMIT applied) when validation succeeded and the original text
    otherwise. Executors must run ``sql`` only when ``ok`` is true and never the
    original input string.
    """

    ok: bool
    sql: str
    referenced_tables: tuple[str, ...] = ()
    referenced_columns: tuple[str, ...] = ()
    limit_injected: bool = False
    errors: tuple[ValidationIssue, ...] = field(default=())
    warnings: tuple[ValidationIssue, ...] = field(default=())


def failure(sql: str, *issues: ValidationIssue) -> ValidationResult:
    """Build a failed result carrying ``issues`` as errors."""
    return ValidationResult(ok=False, sql=sql, errors=tuple(issues))
