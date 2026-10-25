"""The root-cause subagent gets ``search_catalog`` when the orchestrator is given it (RED).

``build_catalog_wiring`` (``rootcause.catalog.agent_tools``) yields an executor,
``table_docs`` and ``tools`` (``search_catalog``); callers pass them to
``build_orchestrator(executor=..., table_docs=..., extra_tools=...)``. The subagent
does the schema inspection, so ``search_catalog`` must reach it too:
``ROOT_CAUSE_TOOLS`` names it and extra tools in that set are handed on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause.settings import Settings

pytestmark = pytest.mark.unit


def _settings() -> Settings:
    return Settings(
        _env_file=None, database_url="postgresql://u:p@localhost/db", environment="test"
    )


@tool
def search_catalog(query: str, k: int = 5) -> dict[str, Any]:
    """Find catalog tables and columns similar to ``query``."""
    return {"results": []}


@tool
def unrelated_helper(text: str) -> str:
    """A tool meant for the orchestrator only."""
    return text


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


def test_root_cause_tools_include_search_catalog() -> None:
    from rootcause.agents.subagents import ROOT_CAUSE_TOOLS

    assert "search_catalog" in ROOT_CAUSE_TOOLS


def test_search_catalog_extra_tool_reaches_the_root_cause_subagent(
    spy: list[dict[str, Any]], scripted_model: Callable[..., Any], executor: Any
) -> None:
    _build(
        scripted_model(AIMessage(content="x")),
        executor=executor,
        extra_tools=[search_catalog, unrelated_helper],
    )

    kwargs = spy[-1]
    [subagent] = kwargs["subagents"]
    assert {t.name for t in subagent["tools"]} == {"run_sql", "explain_sql", "search_catalog"}
    assert {"search_catalog", "unrelated_helper"} <= {t.name for t in kwargs["tools"]}


def test_without_an_executor_extra_tools_create_no_subagent(
    spy: list[dict[str, Any]], scripted_model: Callable[..., Any]
) -> None:
    _build(scripted_model(AIMessage(content="x")), extra_tools=[search_catalog])

    assert spy[-1]["subagents"] == []
