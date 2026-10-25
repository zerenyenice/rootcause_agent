"""The run_sql approval interrupt tells the human which SQL and how many rows (RED)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause.settings import Settings

BIG = 12_345_678
SQL = "select * from big_table where marker = 'X'"


def _request(executor: Any, scripted_model: Any, tool_call_message: Any) -> dict[str, Any]:
    from rootcause.agents.orchestrator import build_orchestrator

    model = scripted_model(
        tool_call_message("run_sql", {"sql": SQL, "purpose": "p"}, "c1"), AIMessage(content="done")
    )
    handle = build_orchestrator(
        model=model,
        settings=Settings(
            _env_file=None, database_url="postgresql://u:p@localhost/db", environment="test"
        ),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )
    result = handle.graph.invoke(
        {"messages": [HumanMessage("go")]}, config={"configurable": {"thread_id": "t"}}
    )
    (interrupt,) = result["__interrupt__"]
    (request,) = interrupt.value["action_requests"]
    assert isinstance(request, dict)
    return request


@pytest.mark.unit
def test_description_contains_the_sql_and_the_estimated_rows(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any], make_executor: Any
) -> None:
    request = _request(make_executor(estimated_rows=BIG), scripted_model, tool_call_message)

    description = request["description"]
    assert SQL in description
    assert str(BIG) in description or f"{BIG:,}" in description


@pytest.mark.unit
def test_description_still_names_the_sql_when_the_estimate_is_unavailable(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any], make_executor: Any
) -> None:
    executor = make_executor(error=RuntimeError("conn reset"))

    request = _request(executor, scripted_model, tool_call_message)

    assert SQL in request["description"]
    assert "conn reset" not in request["description"]
