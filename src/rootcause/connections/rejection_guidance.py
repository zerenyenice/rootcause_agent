"""Plain-words rewrite guidance for each rejection code, shown to the model.

Only static text: no SQL, identifiers or values. Codes raised by the validator also carry
a per-statement message (names the function, table or column); this adds how to fix it.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

REJECTION_GUIDANCE = MappingProxyType(
    {
        "forbidden_function": (
            "Use standard aggregate, window, date/time, string and math functions "
            "(sum, avg, quantile_cont, lag, date_trunc, date_diff, strftime, ...); "
            "functions that read files, settings or the system are not available."
        ),
        "not_read_only": (
            "Only a single read-only SELECT (CTEs, joins, window functions allowed) over "
            "catalog tables is permitted; no writes, DDL, SET/PRAGMA, hints or file access."
        ),
        "generator_function": (
            "Row generators (range, generate_series, unnest, repeat) are not allowed; "
            "join a catalog table (for example distinct dates from the fact table) instead."
        ),
        "unknown_table": "Query only tables returned by list_tables, spelled as listed.",
        "unknown_column": "Use column names from describe_table for that table.",
        "ambiguous_column": "Qualify the column with its table alias.",
        "ambiguous_table": "Qualify the table with its schema.",
        "multiple_statements": "Send exactly one statement, without a second after a semicolon.",
        "parse_error": "Fix the SQL syntax (DuckDB dialect).",
        "input_too_large": "Shorten the query; split it into smaller steps.",
        "result_too_large": "Aggregate or select fewer columns/rows.",
        "star_over_hidden_columns": (
            "Some columns of this table are not selected for the investigation; "
            "name the columns you need instead of using *."
        ),
        "hidden_column": "That column is not selected for the investigation; use listed columns.",
        "unresolvable_reference": (
            "A column or source could not be traced to a selected table; reference columns "
            "through a table alias and avoid sources whose columns are unknown "
            "(VALUES, table functions, DISTINCT ON over unresolved names)."
        ),
        "implicit_join_columns": "Join with an explicit ON condition instead of NATURAL/USING.",
        "renamed_columns": "Do not rename columns with a table alias list; alias them in SELECT.",
        "row_reference": "Select named columns; do not reference a whole row by its table alias.",
    }
)


def guidance_for(codes: Iterable[str]) -> tuple[str, ...]:
    """Distinct guidance lines for ``codes`` (unknown codes contribute nothing)."""
    return tuple(dict.fromkeys(REJECTION_GUIDANCE[c] for c in codes if c in REJECTION_GUIDANCE))
