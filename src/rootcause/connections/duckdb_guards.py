"""Pre-execution and fetch-time guards for the DuckDB warehouse executor.

* :func:`generator_function_found` spots row/value generators (``range``,
  ``generate_series``, ``unnest``, ``repeat``) anywhere in a statement. They turn a
  tiny input into an arbitrarily large intermediate result, so the executor rejects
  them before anything runs.
* :class:`ResultBudget` tracks an estimate of the bytes fetched into Python and
  raises as soon as the running total exceeds the budget.

Neither function puts SQL text or row values into an exception.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import sqlglot
from sqlglot import exp

GENERATOR_FUNCTION_NAMES = frozenset({"range", "generate_series", "unnest", "repeat"})
GENERATOR_FUNCTION_CODE = "generator_function"
RESULT_TOO_LARGE_CODE = "result_too_large"

# Typed sqlglot classes the generator names parse to (``unnest`` in a select list is
# ``Explode``; ``range`` is ``GenerateSeries``). Subclasses match through isinstance.
_GENERATOR_CLASSES: tuple[type[exp.Expr], ...] = tuple(
    cls
    for cls in (
        getattr(exp, name, None)
        for name in ("GenerateSeries", "Explode", "ExplodeOuter", "Unnest", "Repeat")
    )
    if isinstance(cls, type)
)
_SCALAR_BYTES = 16
_CONTAINER_OVERHEAD_BYTES = 16


class ResultTooLarge(Exception):
    """The fetched result exceeded the byte budget (carries no row values)."""


def _is_generator(node: exp.Expr) -> bool:
    if isinstance(node, _GENERATOR_CLASSES):
        return True
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower() in GENERATOR_FUNCTION_NAMES
    if isinstance(node, exp.Func):
        names = {name.lower() for name in type(node).sql_names()}
        return not names.isdisjoint(GENERATOR_FUNCTION_NAMES)
    return False


def generator_function_found(sql: str, dialect: str) -> bool:
    """Whether ``sql`` calls a generator function in any position (scalar, table, lateral).

    Raises:
        sqlglot.errors.ParseError: when ``sql`` does not parse (callers fail closed).
    """
    return any(
        _is_generator(node)
        for statement in sqlglot.parse(sql, read=dialect)
        if statement is not None
        for node in statement.walk()
    )


def estimate_value_bytes(value: Any) -> int:
    """Rough in-memory size of one fetched value: lengths for text, recursion for nesting."""
    if isinstance(value, str | bytes | bytearray):
        return len(value)
    if isinstance(value, Mapping):
        return _CONTAINER_OVERHEAD_BYTES + sum(
            estimate_value_bytes(key) + estimate_value_bytes(item) for key, item in value.items()
        )
    if isinstance(value, list | tuple | set | frozenset):
        return _CONTAINER_OVERHEAD_BYTES + sum(estimate_value_bytes(item) for item in value)
    return _SCALAR_BYTES


class ResultBudget:
    """Running byte total for one fetch; raises :class:`ResultTooLarge` past ``limit``."""

    __slots__ = ("_limit", "_total")

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._total = 0

    def charge(self, values: Iterable[Any]) -> None:
        """Add each value's estimate, failing as soon as the total exceeds the budget."""
        for value in values:
            self._total += estimate_value_bytes(value)
            if self._total > self._limit:
                raise ResultTooLarge()
