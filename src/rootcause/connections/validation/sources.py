"""Table-source rules: no files, URLs, stages, variables or dynamic table references."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from typing import Final

from sqlglot import exp

from rootcause.connections.validation.result import IssueCode, ValidationIssue

_PATH_SUFFIXES: Final[tuple[str, ...]] = (".csv", ".parquet", ".json", ".jsonl", ".tsv")
#: DuckDB resolves a table name that looks like a file path to a file scan, so names there
#: are limited to plain identifier characters (compared after NFKC normalisation).
_DUCKDB_PART: Final = re.compile(r"[A-Za-z0-9_$]+")


def source_issues(statement: exp.Expr, dialect: str) -> Iterator[ValidationIssue]:
    """Yield ``NOT_READ_ONLY`` for table sources that are not plain, safe table names."""
    for node in statement.find_all(exp.Table, exp.TableFromRows):
        if not isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Func):
                yield _not_read_only("TABLE(...) source is not an allow-listed table function.")
            continue
        source = node.this
        if isinstance(source, (exp.Func, exp.Dot)):
            continue  # calls are judged by function_issues
        parts = tuple(part for part in (node.catalog, node.db, node.name) if part)
        if not isinstance(source, exp.Identifier):
            yield _not_read_only("Table source is not a plain table name.")
        elif any(_looks_like_path(part) for part in parts):
            yield _not_read_only(
                f"Table name {node.name!r} looks like a file, URL or stage reference."
            )
        elif dialect == "duckdb" and not all(_is_plain_duckdb_part(part) for part in parts):
            yield _not_read_only(
                f"Table name {node.name!r} contains characters DuckDB may treat as a file path."
            )


def _is_plain_duckdb_part(part: str) -> bool:
    return _DUCKDB_PART.fullmatch(unicodedata.normalize("NFKC", part)) is not None


def _not_read_only(message: str) -> ValidationIssue:
    return ValidationIssue(IssueCode.NOT_READ_ONLY, message)


def _looks_like_path(part: str) -> bool:
    lowered = part.lower()
    return (
        "/" in lowered
        or "\\" in lowered
        or lowered.startswith("@")
        or lowered.endswith(_PATH_SUFFIXES)
    )
