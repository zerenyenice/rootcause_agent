"""Unattended mode (RED): the orchestrator must know no user is available.

Seams: ``investigate()`` wraps the question in an unattended-run instruction (first
human message, question verbatim); ``orchestrator.md`` allows delegation when the
missing what/when/where/impact fields can be measured from the data; the root-cause
system prompt must not point at ``references/pitfalls.md`` unless its content is included.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause.agents.investigate import investigate
from rootcause.agents.orchestrator import build_orchestrator
from rootcause.agents.reports import WhyChain
from rootcause.agents.skills import SKILL_FILE, load_skill_body, skill_paths
from rootcause.settings import Settings

from .test_investigate import CHAIN, TASK

EVAL_QUESTION = (
    "Why did scrap_rate go up by about 200% between 2025-12-11 and 2025-12-15 for line L01?"
)


def _settings() -> Settings:
    return Settings(
        _env_file=None, database_url="postgresql://u:p@localhost/db", environment="test"
    )


def _handle(model: Any, executor: Any) -> Any:
    return build_orchestrator(
        model=model,
        settings=_settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )


class _PayloadSpy:
    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self.payloads: list[Any] = []

    def invoke(self, payload: Any, config: Any = None, **kwargs: Any) -> Any:
        self.payloads.append(payload)
        return self._graph.invoke(payload, config, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


@pytest.mark.unit
def test_first_human_message_is_marked_unattended_and_keeps_the_question_verbatim(
    scripted_model: Callable[..., Any], executor: Any
) -> None:
    real = _handle(scripted_model(AIMessage(content="no chain")), executor)
    handle = dataclasses.replace(real, graph=_PayloadSpy(real.graph))

    investigate(EVAL_QUESTION, handle=handle)

    first = handle.graph.payloads[0]["messages"][0]
    text = str(first.content)
    lowered = text.lower()
    assert EVAL_QUESTION in text
    assert "no user" in lowered
    assert "root-cause" in lowered
    assert "measure" in lowered
    for field in ("what", "when", "where", "impact"):
        assert field in lowered


@pytest.mark.unit
def test_orchestrator_prompt_allows_delegation_when_missing_fields_can_be_measured() -> None:
    from importlib import resources

    prompt = (
        resources.files("rootcause.agents.prompts").joinpath("orchestrator.md").read_text("utf-8")
    ).lower()

    assert "measured" in prompt


@pytest.mark.unit
def test_eval_shaped_question_with_a_delegating_orchestrator_returns_the_chain(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any], executor: Any
) -> None:
    """Guard: may already pass; must keep passing."""
    model = scripted_model(
        tool_call_message("task", TASK, "t1"),
        tool_call_message("run_sql", {"sql": "select 1", "purpose": "p"}, "q1"),
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="final report"),
    )

    chain = investigate(EVAL_QUESTION, handle=_handle(model, executor))

    assert chain == WhyChain.model_validate(CHAIN)


@pytest.mark.unit
def test_root_cause_prompt_does_not_reference_unreadable_pitfalls_file() -> None:
    prompt = load_skill_body("root-cause")
    skill_dir = next(path for path in skill_paths() if path.name == "root-cause")
    pitfalls = (skill_dir / "references" / "pitfalls.md").read_text(encoding="utf-8").strip()

    mentioned = "pitfalls.md" in prompt
    content_included = pitfalls[:200] in prompt
    assert not mentioned or content_included, SKILL_FILE
