"""The root-cause subagent always ends in an answer the orchestrator can trust (RED, #138).

Spec:
1. When the subagent's model-call or tool-call budget runs out before it produced a
   WhyChain, the ``task`` result is still a WhyChain JSON with ``partial=True``.
2. Defence in depth: a ``task`` result that is empty or not a WhyChain reaches the
   orchestrator as an explicit failure marker containing "did not complete", and
   the orchestrator prompt says never to report findings absent from a task result.

Assumptions about the fake model (tests/agents/routing_model.py): mid-run it never
volunteers a WhyChain. A forced final call is recognised when the only tool bound is
``WhyChain`` or ``tool_choice`` names ``WhyChain``; the model then answers with a
partial WhyChain (``cooperate=True``) or ignores the constraint (``cooperate=False``).
The tests assert outcomes, not the forcing mechanism.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause.agents.orchestrator import build_orchestrator
from rootcause.agents.reports import WhyChain
from rootcause.settings import Settings

from .routing_model import RoutingChatModel, routing_model

FAILURE_MARKER = "did not complete"
RECURSION_LIMIT = 500


def _settings(*, max_tool_calls: int = 40, max_steps: int = 80) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        environment="test",
        investigation_max_tool_calls=max_tool_calls,
        investigation_max_steps=max_steps,
    )


def _run(model: RoutingChatModel, executor: Any, settings: Settings) -> list[Any]:
    handle = build_orchestrator(
        model=model,
        settings=settings,
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )
    result = handle.graph.invoke(
        {"messages": [HumanMessage("Why did scrap rise on L01?")]},
        config={"configurable": {"thread_id": "t"}, "recursion_limit": RECURSION_LIMIT},
    )
    return list(result["messages"])


def _task_result(messages: list[Any]) -> str:
    results = [m for m in messages if isinstance(m, ToolMessage) and m.name == "task"]
    assert len(results) == 1, "the orchestrator delegated exactly once"
    return str(results[0].content)


def _task_result_seen_by_orchestrator(model: RoutingChatModel) -> str:
    calls = [c for c in model.recorder.calls if c.role == "orchestrator"]
    assert len(calls) >= 2, "the orchestrator was called again after the task returned"
    return _task_result(list(calls[-1].messages))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reply", "max_tool_calls", "max_steps"),
    [
        pytest.param("sql", 40, 6, id="model-call-budget"),
        pytest.param("parallel_sql", 2, 40, id="tool-call-budget"),
    ],
)
def test_exhausted_budget_still_returns_a_partial_why_chain(
    executor: Any, reply: Any, max_tool_calls: int, max_steps: int
) -> None:
    model = routing_model(subagent_reply=reply)
    settings = _settings(max_tool_calls=max_tool_calls, max_steps=max_steps)

    content = _task_result(_run(model, executor, settings))

    chain = WhyChain.model_validate_json(content)
    assert chain.partial is True


@pytest.mark.unit
def test_orchestrator_receives_the_partial_chain_after_the_budget_ends(executor: Any) -> None:
    model = routing_model(subagent_reply="sql")

    _run(model, executor, _settings(max_steps=6))

    chain = WhyChain.model_validate_json(_task_result_seen_by_orchestrator(model))
    assert chain.partial is True


@pytest.mark.unit
def test_investigate_returns_the_partial_chain_when_the_budget_ends(executor: Any) -> None:
    from rootcause.agents.investigate import investigate

    model = routing_model(subagent_reply="sql")
    handle = build_orchestrator(
        model=model,
        settings=_settings(max_steps=6),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )

    chain = investigate("Why did scrap rise on L01?", handle=handle)

    assert isinstance(chain, WhyChain)
    assert chain.partial is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reply", "refusal", "max_steps"),
    [
        pytest.param("empty", "", 80, id="empty-final-message"),
        pytest.param("text", "Scrap rose because of the die change.", 80, id="prose-no-chain"),
        pytest.param("sql", "", 6, id="budget-ended-and-forced-call-ignored"),
    ],
)
def test_missing_chain_reaches_the_orchestrator_as_an_explicit_failure(
    executor: Any, reply: Any, refusal: str, max_steps: int
) -> None:
    model = routing_model(subagent_reply=reply, cooperate=False, refusal=refusal)

    _run(model, executor, _settings(max_steps=max_steps))

    content = _task_result_seen_by_orchestrator(model)
    assert content.strip(), "the orchestrator must never see an empty task result"
    assert FAILURE_MARKER in content.lower()


@pytest.mark.unit
def test_orchestrator_prompt_forbids_findings_absent_from_a_task_result() -> None:
    from rootcause.agents.prompts import load_packaged_prompt

    prompt = " ".join(load_packaged_prompt("orchestrator").lower().split())

    assert "never report findings" in prompt
    assert FAILURE_MARKER in prompt
