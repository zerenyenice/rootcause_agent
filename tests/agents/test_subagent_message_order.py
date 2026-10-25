"""Every history sent to the model is valid for OpenAI-style providers (RED, #139, #132).

Azure OpenAI rejects a request (400) when a ``tool`` message does not answer a
``tool_calls`` entry of a preceding assistant message, including a second reply to
an already-answered id. The fake model records every message list it receives; the
invariant is checked on all of them:

- each ToolMessage answers a tool_call id of a preceding AIMessage,
- no tool_call_id is answered twice,
- every tool call of an AIMessage is answered before the next AI/Human message.

The failing case (today): parallel run_sql calls over the tool budget whose scans
need approval in an unattended run. The approval rejection and the tool-call limit
both answer the same call, so the next request carries a duplicate tool message.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause.agents.orchestrator import build_orchestrator
from rootcause.settings import Settings

from .routing_model import RoutingChatModel, routing_model

BIG = 50_000_000  # over the approval threshold: every run_sql interrupts


def _settings(*, max_tool_calls: int, max_steps: int) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        environment="test",
        investigation_max_tool_calls=max_tool_calls,
        investigation_max_steps=max_steps,
    )


def _handle(model: RoutingChatModel, executor: Any, settings: Settings) -> Any:
    return build_orchestrator(
        model=model,
        settings=settings,
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )


def history_violations(messages: Sequence[BaseMessage]) -> list[str]:
    """OpenAI tool-message ordering violations in one request's message list."""
    problems: list[str] = []
    called: set[str] = set()
    answered: set[str] = set()
    pending: set[str] = set()
    for index, message in enumerate(messages):
        if isinstance(message, ToolMessage):
            call_id = message.tool_call_id
            if call_id not in called:
                problems.append(f"[{index}] orphan tool message {call_id}")
            elif call_id in answered:
                problems.append(f"[{index}] duplicate tool message {call_id}")
            answered.add(call_id)
            pending.discard(call_id)
            continue
        if isinstance(message, AIMessage | HumanMessage) and pending:
            problems.append(f"[{index}] unanswered tool calls {sorted(pending)}")
            pending = set()
        if isinstance(message, AIMessage):
            ids = {str(call["id"]) for call in message.tool_calls}
            called |= ids
            pending = ids
    return problems


def _assert_every_request_valid(model: RoutingChatModel) -> None:
    assert model.recorder.calls, "the model was called"
    found = {
        number: problems
        for number, call in enumerate(model.recorder.calls)
        if (problems := history_violations(call.messages))
    }
    if found:
        first = min(found)
        role = model.recorder.calls[first].role
        pytest.fail(f"call {first} ({role}) sends an invalid history: {found[first][:3]}")


@pytest.mark.unit
def test_history_violations_detects_orphans_duplicates_and_unanswered_calls() -> None:
    call = AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {}, "id": "a"}])
    answer = ToolMessage(content="ok", tool_call_id="a")

    assert history_violations([HumanMessage("q"), call, answer]) == []
    assert history_violations([HumanMessage("q"), answer]) == ["[1] orphan tool message a"]
    assert history_violations([call, answer, answer]) == ["[2] duplicate tool message a"]
    assert history_violations([call, AIMessage("x")]) == ["[1] unanswered tool calls ['a']"]


@pytest.mark.unit
def test_rejected_parallel_calls_over_the_tool_budget_keep_every_request_valid(
    monkeypatch: pytest.MonkeyPatch, make_executor: Any
) -> None:
    import rootcause.agents.investigate as module

    monkeypatch.setattr(module, "MAX_RESUMES", 8)
    model = routing_model(subagent_reply="parallel_sql", parallel=3)
    executor = make_executor(estimated_rows=BIG)
    handle = _handle(model, executor, _settings(max_tool_calls=2, max_steps=10))

    module.investigate("Why did scrap rise on L01?", handle=handle)

    _assert_every_request_valid(model)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("max_tool_calls", "max_steps"),
    [
        pytest.param(4, 10, id="model-call-limit-ends"),
        pytest.param(2, 40, id="tool-limit-continue"),
    ],
)
def test_parallel_calls_over_the_tool_budget_keep_every_request_valid(
    executor: Any, max_tool_calls: int, max_steps: int
) -> None:
    """Guard (passes today without approvals): must keep passing after the fix."""
    from rootcause.agents.investigate import investigate

    model = routing_model(subagent_reply="parallel_sql", parallel=3)
    handle = _handle(model, executor, _settings(max_tool_calls=max_tool_calls, max_steps=max_steps))

    investigate("Why did scrap rise on L01?", handle=handle)

    _assert_every_request_valid(model)
