"""T030: orchestrator end to end with a scripted model (no network, no real LLM)."""

from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from rootcause_core.settings import Settings

THRESHOLD = 5_000_000
PROBE = {"sql": "SELECT 1", "purpose": "probe"}
TODOS = [
    {"content": "Frame the problem", "status": "in_progress"},
    {"content": "Check order volume", "status": "pending"},
]


def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        environment="test",
        scan_row_threshold=THRESHOLD,
    )


def script(scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any]) -> Any:
    return scripted_model(
        tool_call_message("write_todos", {"todos": TODOS}, "c1"),
        tool_call_message("run_sql", PROBE, "c2"),
        AIMessage(content="done"),
    )


def build(
    executor: Any, scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any]
) -> Any:
    from rootcause_agents.orchestrator import build_orchestrator

    return build_orchestrator(
        model=script(scripted_model, tool_call_message),
        settings=settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )


def config(thread: str = "t1") -> RunnableConfig:
    return {"configurable": {"thread_id": thread}}


FIRST = {"messages": [HumanMessage("orders dropped 18% last Tuesday, why?")]}


@pytest.mark.unit
def test_handle_exposes_graph_store_and_checkpointer(
    executor: Any, scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any]
) -> None:
    handle = build(executor, scripted_model, tool_call_message)
    assert handle.graph is not None
    assert isinstance(handle.store, InMemoryStore)
    assert isinstance(handle.checkpointer, InMemorySaver)


@pytest.mark.unit
def test_end_to_end_plans_queries_and_finishes(
    executor: Any, scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any]
) -> None:
    handle = build(executor, scripted_model, tool_call_message)
    result = handle.graph.invoke(FIRST, config=config())

    assert "__interrupt__" not in result
    todos = handle.graph.get_state(config()).values["todos"]
    assert [t["content"] for t in todos] == [t["content"] for t in TODOS]
    assert len(todos) == 2
    sql_messages = [
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "c2"
    ]
    assert len(sql_messages) == 1
    assert "SELECT 1" in str(sql_messages[0].content)
    assert "row_count" in str(sql_messages[0].content)
    assert executor.run_calls == [("SELECT 1", "probe")]
    assert result["messages"][-1].content == "done"


@pytest.mark.unit
def test_second_invoke_on_same_thread_sees_prior_messages(
    scripted_model: Callable[..., Any],
) -> None:
    from rootcause_agents.orchestrator import build_orchestrator

    model = scripted_model(AIMessage(content="first"), AIMessage(content="second"))
    handle = build_orchestrator(
        model=model, settings=settings(), store=InMemoryStore(), checkpointer=InMemorySaver()
    )
    first = handle.graph.invoke(FIRST, config=config())
    followup = handle.graph.invoke({"messages": [HumanMessage("and Wednesday?")]}, config=config())
    assert len(followup["messages"]) > len(first["messages"])
    assert any(
        isinstance(m, HumanMessage) and "dropped 18%" in str(m.content)
        for m in followup["messages"]
    )
    assert followup["messages"][-1].content == "second"


@pytest.mark.unit
def test_other_thread_does_not_see_prior_messages(scripted_model: Callable[..., Any]) -> None:
    from rootcause_agents.orchestrator import build_orchestrator

    model = scripted_model(AIMessage(content="first"), AIMessage(content="second"))
    handle = build_orchestrator(
        model=model, settings=settings(), store=InMemoryStore(), checkpointer=InMemorySaver()
    )
    handle.graph.invoke(FIRST, config=config("a"))
    other = handle.graph.invoke({"messages": [HumanMessage("hi")]}, config=config("b"))
    assert not any("dropped 18%" in str(m.content) for m in other["messages"])


@pytest.mark.unit
def test_expensive_query_interrupts_then_resumes_on_approval(
    make_executor: Callable[..., Any],
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
) -> None:
    executor = make_executor(estimated_rows=10_000_000)
    handle = build(executor, scripted_model, tool_call_message)
    result = handle.graph.invoke(FIRST, config=config())

    assert "__interrupt__" in result
    assert handle.graph.get_state(config()).next
    assert executor.run_calls == []

    # Documented human-in-the-loop shape: one decision per gated tool call.
    resumed = handle.graph.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config=config()
    )
    assert "__interrupt__" not in resumed
    assert not handle.graph.get_state(config()).next
    assert executor.run_calls == [("SELECT 1", "probe")]
    assert resumed["messages"][-1].content == "done"


@pytest.mark.unit
def test_extra_tools_are_available_to_the_model(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any]
) -> None:
    from langchain_core.tools import tool

    from rootcause_agents.orchestrator import build_orchestrator

    @tool
    def ping() -> str:
        """Return pong."""
        return "pong"

    model = scripted_model(tool_call_message("ping", {}, "p1"), AIMessage(content="ok"))
    handle = build_orchestrator(
        model=model,
        settings=settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        extra_tools=(ping,),
    )
    result = handle.graph.invoke(FIRST, config=config())
    assert any(isinstance(m, ToolMessage) and m.content == "pong" for m in result["messages"])
