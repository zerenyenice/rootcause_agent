"""build_orchestrator wires the root-cause subagent; the prompt frames and delegates (RED)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause.settings import Settings

CHAIN: dict[str, Any] = {
    "problem": {"what": "scrap up", "when": "May", "where": "Line 3", "impact": "10k EUR"},
    "levels": [
        {
            "question": f"why {i}?",
            "answer": f"because {i}",
            "evidence": [{"sql": f"select {i}", "rows_summary": "1 row"}],
            "confidence": 0.8,
        }
        for i in range(3)
    ],
    "root_cause": {"statement": "no SOP", "type": "process", "confidence": 0.7},
    "validation": [{"link": "1->2", "check_sql": "select 1", "passed": True}],
}


def _settings() -> Settings:
    return Settings(
        _env_file=None, database_url="postgresql://u:p@localhost/db", environment="test"
    )


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import rootcause.agents.orchestrator as module

    calls: list[dict[str, Any]] = []
    real = vars(module)["create_deep_agent"]

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "create_deep_agent", wrapper)
    return calls


def _build(model: Any, **kwargs: Any) -> Any:
    from rootcause.agents.orchestrator import build_orchestrator

    return build_orchestrator(
        model=model,
        settings=_settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        **kwargs,
    )


@pytest.mark.unit
def test_executor_adds_the_root_cause_subagent(
    spy: list[dict[str, Any]], scripted_model: Callable[..., Any], executor: Any
) -> None:
    _build(scripted_model(AIMessage(content="x")), executor=executor)

    subagents = spy[-1]["subagents"]
    assert [s["name"] for s in subagents] == ["root-cause"]
    assert {t.name for t in subagents[0]["tools"]} == {"run_sql", "explain_sql"}


@pytest.mark.unit
def test_without_an_executor_there_is_no_root_cause_subagent(
    spy: list[dict[str, Any]], scripted_model: Callable[..., Any]
) -> None:
    _build(scripted_model(AIMessage(content="x")))

    assert "root-cause" not in [s["name"] for s in spy[-1].get("subagents") or []]


@pytest.mark.unit
def test_orchestrator_delegates_to_the_subagent_and_gets_the_why_chain_back(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any], executor: Any
) -> None:
    from rootcause.agents.reports import WhyChain

    model = scripted_model(
        tool_call_message(
            "task", {"description": "why did scrap rise?", "subagent_type": "root-cause"}, "t1"
        ),
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="report"),
    )
    handle = _build(model, executor=executor)

    result = handle.graph.invoke(
        {"messages": [HumanMessage("why did scrap rise?")]},
        config={"configurable": {"thread_id": "t"}},
    )

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    chains = [
        WhyChain.model_validate_json(str(m.content)) for m in tool_messages if m.name == "task"
    ]
    assert [c.root_cause.statement for c in chains if c.root_cause] == ["no SOP"]


@pytest.mark.unit
def test_orchestrator_prompt_delegates_and_relays_partial_notes() -> None:
    from rootcause.agents.prompts import load_packaged_prompt

    prompt = load_packaged_prompt("orchestrator").lower()

    assert "root-cause" in prompt
    assert "task" in prompt  # delegation goes through the task tool
    assert "why" in prompt
    assert "notes" in prompt
    assert "partial" in prompt
    for field in ("what", "when", "where", "impact"):
        assert field in prompt


@pytest.mark.unit
def test_task_tool_message_is_exactly_the_why_chain_json(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any], executor: Any
) -> None:
    from rootcause.agents.reports import WhyChain

    model = scripted_model(
        tool_call_message(
            "task", {"description": "why did scrap rise?", "subagent_type": "root-cause"}, "t1"
        ),
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="report"),
    )
    handle = _build(model, executor=executor)

    result = handle.graph.invoke(
        {"messages": [HumanMessage("why did scrap rise?")]},
        config={"configurable": {"thread_id": "t"}},
    )

    [task] = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "task"]
    chain = WhyChain.model_validate_json(str(task.content))
    assert task.content == chain.model_dump_json()
