"""investigate() budgets (RED): subagent tool calls, wall clock, resume-cap logging.

Seams the implementation must provide:
- ``Settings.investigation_max_tool_calls`` (int > 0, default 60, env
  ``ROOTCAUSE_INVESTIGATION_MAX_TOOL_CALLS``): tool-call budget of the root-cause
  subagent (ToolCallLimitMiddleware or equivalent) plus a recursion limit for it
  derived from ``investigation_max_steps``.
- ``Settings.investigation_timeout_seconds`` (int > 0, default 600, env
  ``ROOTCAUSE_INVESTIGATION_TIMEOUT_SECONDS``): wall-clock budget across resumes.
- Clock seam: ``rootcause.agents.investigate`` does ``import time`` and reads
  ``time.monotonic()``; tests replace the module attribute ``time``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError
from structlog.testing import capture_logs

from rootcause.agents.investigate import investigate
from rootcause.agents.orchestrator import build_orchestrator
from rootcause.agents.reports import WhyChain
from rootcause.settings import Settings

from .test_investigate import BIG, CHAIN, QUESTION, TASK, _SpyGraph


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        environment="test",
        **overrides,
    )


def _handle(model: Any, executor: Any, settings: Settings) -> Any:
    return build_orchestrator(
        model=model,
        settings=settings,
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )


def _sql(i: int, prefix: str = "q") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "run_sql",
                "args": {"sql": f"select {i}", "purpose": "p"},
                "id": f"{prefix}{i}",
            }
        ],
    )


@pytest.mark.unit
def test_subagent_tool_call_budget_bounds_run_sql_and_the_run_still_returns(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any], executor: Any
) -> None:
    settings = _settings(investigation_max_tool_calls=5, investigation_max_steps=500)
    model = scripted_model(
        tool_call_message("task", TASK, "t1"),
        *[_sql(i) for i in range(40)],
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="final report"),
    )

    chain = investigate(QUESTION, handle=_handle(model, executor, settings))

    assert len(executor.run_calls) <= 5
    assert chain is None or isinstance(chain, WhyChain)


@pytest.mark.unit
def test_tool_call_budget_default_env_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings().model_dump()["investigation_max_tool_calls"] == 60

    monkeypatch.setenv("ROOTCAUSE_INVESTIGATION_MAX_TOOL_CALLS", "7")
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    assert Settings(_env_file=None).model_dump()["investigation_max_tool_calls"] == 7


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, -1])
def test_tool_call_budget_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        _settings(investigation_max_tool_calls=value)


@pytest.mark.unit
def test_timeout_default_env_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings().model_dump()["investigation_timeout_seconds"] == 600

    monkeypatch.setenv("ROOTCAUSE_INVESTIGATION_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    assert Settings(_env_file=None).model_dump()["investigation_timeout_seconds"] == 30

    for bad in (0, -5):
        with pytest.raises(ValidationError):
            _settings(investigation_timeout_seconds=bad)


@pytest.mark.unit
def test_wall_clock_budget_stops_resuming_between_rounds_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    make_executor: Callable[..., Any],
) -> None:
    import rootcause.agents.investigate as module

    ticks = iter(range(0, 100_000, 1_000))  # every clock read is 1000s later
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: float(next(ticks))))
    executor = make_executor(estimated_rows=BIG)  # every run_sql interrupts for approval
    settings = _settings(investigation_timeout_seconds=100, investigation_max_steps=500)
    model = scripted_model(
        tool_call_message("task", TASK, "t1"),
        *[_sql(i) for i in range(10)],
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="final report"),
    )
    real = _handle(model, executor, settings)
    handle = dataclasses.replace(real, graph=_SpyGraph(real.graph))

    chain = investigate(QUESTION, handle=handle)

    assert chain is None
    assert len(handle.graph.configs) <= 2  # the first run, at most one resume
    assert executor.run_calls == []


@pytest.mark.unit
def test_resume_cap_still_logs_investigation_finished(
    monkeypatch: pytest.MonkeyPatch,
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    make_executor: Callable[..., Any],
) -> None:
    import rootcause.agents.investigate as module

    monkeypatch.setattr(module, "MAX_RESUMES", 2)
    executor = make_executor(estimated_rows=BIG)
    settings = _settings(investigation_max_steps=500)
    model = scripted_model(
        tool_call_message("task", TASK, "t1"),
        *[_sql(i) for i in range(10)],
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="final report"),
    )

    with capture_logs() as logs:
        investigate(QUESTION, handle=_handle(model, executor, settings))

    events = [entry["event"] for entry in logs]
    assert "investigation_resume_budget_exhausted" in events
    assert "investigation_finished" in events
