"""LIMIT injection and capping on the outermost query."""

from __future__ import annotations

from typing import NamedTuple

from sqlglot import exp

from rootcause_connections.validation.result import IssueCode, ValidationIssue


class LimitOutcome(NamedTuple):
    """The query after LIMIT handling plus what was done to it."""

    query: exp.Query
    injected: bool
    warning: ValidationIssue | None


def outer_select(query: exp.Query) -> exp.Select | None:
    """Return the outermost SELECT when the query is a (possibly CTE-wrapped) SELECT."""
    body = query.this if isinstance(query, exp.With) else query
    return body if isinstance(body, exp.Select) else None


def is_aggregate_only(query: exp.Query) -> bool:
    """True when the outermost SELECT groups or aggregates, so a LIMIT adds nothing safe."""
    select = outer_select(query)
    if select is None:
        return False
    if select.args.get("group") is not None:
        return True
    return any(
        isinstance(node, exp.AggFunc) and node.find_ancestor(exp.Window, exp.Subquery) is None
        for projection in select.expressions
        for node in projection.walk()
    )


def existing_limit(query: exp.Query) -> int | None:
    """Return the integer value of an existing outermost LIMIT, if any."""
    limit = query.args.get("limit")
    if not isinstance(limit, exp.Limit):
        return None
    value = limit.expression
    if isinstance(value, exp.Literal) and value.is_int:
        return int(value.this)
    return None


def apply_limit(query: exp.Query, default_limit: int) -> LimitOutcome:
    """Inject ``default_limit`` when absent, cap larger limits, never mutate ``query``."""
    current = existing_limit(query)
    if current is None:
        if is_aggregate_only(query) or query.args.get("limit") is not None:
            return LimitOutcome(query, injected=False, warning=None)
        return LimitOutcome(query.limit(default_limit), injected=True, warning=None)
    if current <= default_limit:
        return LimitOutcome(query, injected=False, warning=None)
    warning = ValidationIssue(
        IssueCode.NOT_READ_ONLY,
        f"LIMIT {current} exceeds the maximum of {default_limit} and was capped.",
    )
    return LimitOutcome(query.limit(default_limit), injected=False, warning=warning)
