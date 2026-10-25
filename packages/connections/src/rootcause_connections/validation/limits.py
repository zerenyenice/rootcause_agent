"""LIMIT injection and capping on the outermost query."""

from __future__ import annotations

from typing import Final, NamedTuple

from sqlglot import exp

from rootcause_connections.validation.result import IssueCode, ValidationIssue

#: Longest digit string parsed as an int; anything longer is far above any sane cap.
_MAX_LITERAL_DIGITS: Final = 18


class LimitOutcome(NamedTuple):
    """The query after LIMIT handling plus what was done to it."""

    query: exp.Query
    injected: bool
    warning: ValidationIssue | None


def apply_limit(query: exp.Query, default_limit: int) -> LimitOutcome:
    """Guarantee an outermost ``LIMIT`` of at most ``default_limit``; never mutate ``query``.

    A missing limit is injected. An existing limit survives only when it is an integer
    literal in ``[0, default_limit]``; ``ALL``, ``NULL``, expressions, placeholders,
    subqueries, casts, floats, negatives, percentages, ``WITH TIES`` and non-literal or
    oversized ``FETCH`` clauses are replaced and reported as ``LIMIT_CAPPED``.
    """
    clause = query.args.get("limit")
    if clause is None:
        return LimitOutcome(query.limit(default_limit), injected=True, warning=None)
    if _is_within_cap(clause, default_limit):
        return LimitOutcome(query, injected=False, warning=None)
    warning = ValidationIssue(
        IssueCode.LIMIT_CAPPED,
        f"The row limit is not an integer literal of at most {default_limit}; "
        f"it was replaced with LIMIT {default_limit}.",
    )
    return LimitOutcome(query.limit(default_limit), injected=False, warning=warning)


def _is_within_cap(clause: exp.Expr, cap: int) -> bool:
    if isinstance(clause, exp.Limit):
        value = clause.expression
    elif isinstance(clause, exp.Fetch):
        value = clause.args.get("count")
    else:
        return False
    options = clause.args.get("limit_options")
    if isinstance(options, exp.LimitOptions) and (
        options.args.get("percent") or options.args.get("with_ties")
    ):
        return False
    count = _integer_literal(value)
    return count is not None and 0 <= count <= cap


def _integer_literal(value: exp.Expr | None) -> int | None:
    while isinstance(value, exp.Paren):
        value = value.this
    if not isinstance(value, exp.Literal) or value.is_string or not value.is_int:
        return None
    if len(value.name) > _MAX_LITERAL_DIGITS:
        return None
    return int(value.name)
