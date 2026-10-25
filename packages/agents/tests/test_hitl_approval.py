"""Human-in-the-loop approval is per tool call and fails closed."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from rootcause_core.settings import Settings

THRESHOLD = 5_000_000
BIG = THRESHOLD + 5_000_000
SQL = "select * from big"
ARGS = {"sql": SQL, "purpose": "p"}
REJECT = {"decisions": [{"type": "reject", "message": "no"}]}
APPROVE = {"decisions": [{"type": "approve"}]}


@dataclass
class ToggleExecutor:
    """Recording executor whose explain can fail and whose estimate can change."""

    estimated_rows: int = 10
    explain_fails: bool = False
    explain_calls: list[str] = field(default_factory=list)
    run_calls: list[tuple[str, str]] = field(default_factory=list)

    def explain(self, sql: str) -> Any:
        from rootcause_agents.tools.sql import QueryEstimate

        self.explain_calls.append(sql)
        if self.explain_fails:
            raise RuntimeError("conn reset")
        return QueryEstimate(estimated_rows=self.estimated_rows, estimated_cost=1.0)

    def run(self, sql: str, purpose: str) -> Any:
        from rootcause_agents.tools.sql import QueryResult

        self.run_calls.append((sql, purpose))
        return QueryResult(columns=("n",), rows=((1,),), row_count=1, truncated=False, sql=sql)


@pytest.fixture
def script(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any]
) -> Callable[..., Any]:
    """Return ``script(executor, "c1", "c2", ...)``: ids become run_sql calls, text ends."""

    def _script(executor: Any, *steps: str) -> Any:
        messages = [
            AIMessage(content=step[1:])
            if step.startswith("=")
            else tool_call_message("run_sql", ARGS, step)
            for step in steps
        ]
        return build(executor, scripted_model(*messages))

    return _script


def build(executor: Any, model: Any) -> Any:
    from rootcause_agents.orchestrator import build_orchestrator

    settings = Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        environment="test",
        scan_row_threshold=THRESHOLD,
    )
    return build_orchestrator(
        model=model,
        settings=settings,
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )


def cfg(thread: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread}}


def start(handle: Any, thread: str) -> Any:
    return handle.graph.invoke({"messages": [HumanMessage("go")]}, config=cfg(thread))


@pytest.mark.unit
def test_reject_is_final(script: Callable[..., Any]) -> None:
    ex = ToggleExecutor(estimated_rows=BIG)
    handle = script(ex, "c1", "=done")
    assert "__interrupt__" in start(handle, "t")
    handle.graph.invoke(Command(resume=REJECT), config=cfg("t"))
    assert ex.run_calls == []


@pytest.mark.unit
def test_reject_is_final_when_explain_fails_on_resume(script: Callable[..., Any]) -> None:
    ex = ToggleExecutor(estimated_rows=BIG)
    handle = script(ex, "c1", "=done")
    assert "__interrupt__" in start(handle, "t")
    ex.explain_fails = True
    handle.graph.invoke(Command(resume=REJECT), config=cfg("t"))
    assert ex.run_calls == []


@pytest.mark.unit
def test_reject_is_final_when_estimate_drops_below_threshold(script: Callable[..., Any]) -> None:
    ex = ToggleExecutor(estimated_rows=BIG)
    handle = script(ex, "c1", "=done")
    assert "__interrupt__" in start(handle, "t")
    ex.estimated_rows = 1
    handle.graph.invoke(Command(resume=REJECT), config=cfg("t"))
    assert ex.run_calls == []


@pytest.mark.unit
def test_explain_failure_before_interrupt_fails_closed(script: Callable[..., Any]) -> None:
    ex = ToggleExecutor(explain_fails=True)
    handle = script(ex, "c1", "=done")
    result = start(handle, "t")
    assert "__interrupt__" in result
    assert ex.run_calls == []


@pytest.mark.unit
def test_rejection_on_one_thread_does_not_approve_same_sql_elsewhere(
    script: Callable[..., Any],
) -> None:
    ex = ToggleExecutor(estimated_rows=BIG)
    handle = script(ex, "c1", "=after reject", "c2")
    assert "__interrupt__" in start(handle, "a")
    handle.graph.invoke(Command(resume=REJECT), config=cfg("a"))
    ex.explain_fails = True
    assert "__interrupt__" in start(handle, "b")
    assert ex.run_calls == []


@pytest.mark.unit
def test_approval_covers_only_its_own_tool_call(script: Callable[..., Any]) -> None:
    ex = ToggleExecutor(estimated_rows=BIG)
    handle = script(ex, "c1", "c2", "c3")
    assert "__interrupt__" in start(handle, "a")
    ex.explain_fails = True  # nothing but a stale approval could let a repeat run
    again = handle.graph.invoke(Command(resume=APPROVE), config=cfg("a"))
    assert len(ex.run_calls) == 1
    assert "__interrupt__" in again  # identical call c2 needs its own approval
    assert "__interrupt__" in start(handle, "b")
    assert len(ex.run_calls) == 1


@pytest.mark.unit
def test_cheap_call_explains_at_most_once(script: Callable[..., Any]) -> None:
    ex = ToggleExecutor(estimated_rows=1)
    handle = script(ex, "c1", "=done")
    start(handle, "t")
    assert len(ex.run_calls) == 1
    assert len(ex.explain_calls) <= 1


@pytest.mark.unit
def test_expensive_approved_call_explains_at_most_once(script: Callable[..., Any]) -> None:
    ex = ToggleExecutor(estimated_rows=BIG)
    handle = script(ex, "c1", "=done")
    assert "__interrupt__" in start(handle, "t")
    handle.graph.invoke(Command(resume=APPROVE), config=cfg("t"))
    assert len(ex.run_calls) == 1
    assert len(ex.explain_calls) <= 1


@pytest.mark.unit
def test_tool_message_content_is_valid_json_for_rich_values(
    script: Callable[..., Any],
) -> None:
    import datetime as dt
    import decimal
    import uuid

    from rootcause_agents.tools.sql import QueryResult

    class Rich(ToggleExecutor):
        def run(self, sql: str, purpose: str) -> Any:
            return QueryResult(
                columns=("d", "ts", "day", "id"),
                rows=(
                    (
                        decimal.Decimal("1.5"),
                        dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC),
                        dt.date(2026, 1, 2),
                        uuid.uuid4(),
                    ),
                ),
                row_count=1,
                truncated=False,
                sql=sql,
            )

    handle = script(Rich(estimated_rows=1), "c1", "=done")
    result = start(handle, "t")
    msg = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    payload = json.loads(msg.content)  # type: ignore[arg-type]
    assert payload["row_count"] == 1
    assert "error" not in payload
