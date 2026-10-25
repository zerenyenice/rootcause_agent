"""``run_sql`` / ``explain_sql`` tools over an injected :class:`QueryExecutor`.

The real executor lands later; tools only depend on the protocol. Expensive
queries (estimate above the row threshold) are gated twice: the orchestrator's
human-in-the-loop predicate interrupts before the tool runs, and the tool itself
refuses to run an unapproved expensive query when used directly.
"""

import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.tools import BaseTool, tool


@dataclass(frozen=True)
class QueryEstimate:
    """Planner estimate for a statement."""

    estimated_rows: int
    estimated_cost: float


@dataclass(frozen=True)
class QueryResult:
    """Rows returned by an executed statement."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool
    sql: str


class QueryExecutor(Protocol):
    """Database access the SQL tools depend on."""

    def explain(self, sql: str) -> QueryEstimate: ...

    def run(self, sql: str, purpose: str) -> QueryResult: ...


def _fingerprint(sql: str) -> str:
    normalised = " ".join(sql.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class ApprovalRegistry:
    """Thread-safe set of statements a human approval is pending or granted for.

    The interrupt predicate marks a statement; ``run_sql`` consumes the mark. A
    rejected call never reaches the tool, so any invocation of a marked
    statement implies approval.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[str] = set()

    def mark(self, sql: str) -> None:
        with self._lock:
            self._pending.add(_fingerprint(sql))

    def consume(self, sql: str) -> bool:
        """Remove the mark for ``sql``; return whether it was present."""
        with self._lock:
            key = _fingerprint(sql)
            present = key in self._pending
            self._pending.discard(key)
            return present


def _error(exc: Exception) -> dict[str, Any]:
    return {"error": {"code": type(exc).__name__, "message": str(exc)}}


def _result_payload(result: QueryResult) -> dict[str, Any]:
    return {
        "columns": list(result.columns),
        "rows": [list(row) for row in result.rows],
        "row_count": result.row_count,
        "truncated": result.truncated,
        "sql": result.sql,
    }


def make_sql_tools(
    executor: QueryExecutor,
    threshold: int,
    registry: ApprovalRegistry | None = None,
) -> list[BaseTool]:
    """Build ``explain_sql`` and ``run_sql`` bound to ``executor``."""

    @tool
    def explain_sql(sql: str) -> dict[str, Any]:
        """Estimate rows and cost for a SQL statement without executing it."""
        try:
            estimate = executor.explain(sql)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as a payload
            return _error(exc)
        return {
            "estimated_rows": estimate.estimated_rows,
            "estimated_cost": estimate.estimated_cost,
        }

    @tool
    def run_sql(sql: str, purpose: str) -> dict[str, Any]:
        """Run a read-only SQL statement; `purpose` states why the query is needed."""
        approved = registry is not None and registry.consume(sql)
        try:
            if not approved:
                estimate = executor.explain(sql)
                if estimate.estimated_rows > threshold:
                    return {
                        "approval_required": True,
                        "estimated_rows": estimate.estimated_rows,
                        "threshold": threshold,
                        "sql": sql,
                    }
            return _result_payload(executor.run(sql, purpose))
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as a payload
            return _error(exc)

    return [explain_sql, run_sql]
