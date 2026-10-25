"""``run_sql`` / ``explain_sql`` tools over an injected :class:`QueryExecutor`.

The real executor lands later; tools only depend on the protocol. Expensive
queries (estimate above the row threshold) are gated twice: the orchestrator's
human-in-the-loop predicate interrupts before the tool runs, and the tool itself
refuses to run an unapproved expensive query when used directly.
"""

import base64
import datetime
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Protocol

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolCallId, tool

from rootcause_core.logging import get_logger

_logger = get_logger(__name__)
_ERROR_MESSAGE = "The query could not be executed."
DEFAULT_MAX_DECISIONS = 10_000


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


@dataclass(frozen=True)
class ApprovalDecision:
    """Outcome of the approval predicate for one tool call."""

    requires_approval: bool
    estimate: QueryEstimate | None


DecisionKey = tuple[str, str]


def thread_id_of(config: Any) -> str:
    """Extract ``configurable.thread_id`` from a runnable config (``""`` if absent)."""
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("thread_id", ""))


class ApprovalRegistry:
    """Bounded, thread-safe per-tool-call approval decisions.

    Keyed by ``(thread_id, tool_call_id)`` -- never by SQL text -- so approving
    one call cannot authorise another. The predicate ``decide`` caches its
    decision, so the re-evaluation on resume returns the same answer; ``run_sql``
    ``take``s the entry (removing it). A rejected call never reaches the tool.
    Oldest entries are evicted beyond ``max_entries``.
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_DECISIONS) -> None:
        self._lock = threading.Lock()
        self._max = max_entries
        self._entries: OrderedDict[DecisionKey, ApprovalDecision] = OrderedDict()

    def decide(
        self, key: DecisionKey, evaluate: Callable[[], ApprovalDecision]
    ) -> ApprovalDecision:
        """Return the cached decision for ``key``, computing it once via ``evaluate``."""
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
        decision = evaluate()
        with self._lock:
            self._entries.setdefault(key, decision)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
            return self._entries[key]

    def take(self, key: DecisionKey) -> ApprovalDecision | None:
        """Remove and return the decision for ``key`` (``None`` if absent)."""
        with self._lock:
            return self._entries.pop(key, None)


def _error(exc: Exception, tool_call_id: str | None) -> dict[str, Any]:
    _logger.error(
        "sql_tool_failed",
        tool_call_id=tool_call_id,
        error_type=type(exc).__name__,
        error=str(exc),
        exc_info=exc,
    )
    return {"error": {"code": type(exc).__name__, "message": _ERROR_MESSAGE}}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    return str(value)


def _result_payload(result: QueryResult) -> dict[str, Any]:
    return {
        "columns": list(result.columns),
        "rows": [[_json_value(v) for v in row] for row in result.rows],
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
            return _error(exc, None)
        return {
            "estimated_rows": estimate.estimated_rows,
            "estimated_cost": estimate.estimated_cost,
        }

    @tool
    def run_sql(
        sql: str,
        purpose: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> dict[str, Any]:
        """Run a read-only SQL statement; `purpose` states why the query is needed."""
        decision = (
            registry.take((thread_id_of(config), tool_call_id))
            if registry is not None and tool_call_id
            else None
        )
        try:
            approved = decision is not None and decision.requires_approval
            estimate = decision.estimate if decision is not None else None
            if not approved:
                estimate = estimate if estimate is not None else executor.explain(sql)
                if estimate.estimated_rows > threshold:
                    return {
                        "approval_required": True,
                        "estimated_rows": estimate.estimated_rows,
                        "threshold": threshold,
                        "sql": sql,
                    }
            return _result_payload(executor.run(sql, purpose))
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as a payload
            return _error(exc, tool_call_id)

    return [explain_sql, run_sql]
