"""investigate(): one unattended, bounded run that returns the subagent's WhyChain (RED).

Seam: ``investigate(question, *, handle: OrchestratorHandle | None = None)``. Without
a handle it builds one from settings (``executor_from_settings`` + the eval table docs).
"""

from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

from rootcause.errors import ConfigurationError
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
    "root_cause": {"statement": "no SOP for die change", "type": "process", "confidence": 0.7},
    "validation": [{"link": "1->2", "check_sql": "select 1", "passed": True}],
}
BIG = 50_000_000
TASK = {"description": "why did scrap rise?", "subagent_type": "root-cause"}
QUESTION = "Why did scrap rise on Line 3 in May?"


def _investigate() -> Any:
    # Resolved at call time so the module can be absent while the tests are RED.
    return importlib.import_module("rootcause.agents.investigate").investigate


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        environment="test",
        **overrides,
    )


def _handle(model: Any, executor: Any, settings: Settings | None = None) -> Any:
    from rootcause.agents.orchestrator import build_orchestrator

    return build_orchestrator(
        model=model,
        settings=settings or _settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )


@pytest.mark.unit
def test_happy_path_returns_the_chain_the_subagent_produced(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any], executor: Any
) -> None:
    investigate = _investigate()
    from rootcause.agents.reports import WhyChain

    model = scripted_model(
        tool_call_message("task", TASK, "t1"),
        tool_call_message("run_sql", {"sql": "select 1", "purpose": "p"}, "q1"),
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="final report"),
    )

    chain = investigate(QUESTION, handle=_handle(model, executor))

    assert isinstance(chain, WhyChain)
    assert chain == WhyChain.model_validate(CHAIN)
    assert executor.run_calls == [("select 1", "p")]


@pytest.mark.unit
def test_no_delegation_means_no_chain(scripted_model: Callable[..., Any], executor: Any) -> None:
    investigate = _investigate()

    model = scripted_model(AIMessage(content="Please quantify the impact first."))

    assert investigate(QUESTION, handle=_handle(model, executor)) is None


@pytest.mark.unit
def test_empty_question_still_runs_and_returns_none_without_a_chain(
    scripted_model: Callable[..., Any], executor: Any
) -> None:
    investigate = _investigate()

    model = scripted_model(AIMessage(content="what do you want to know?"))

    assert investigate("", handle=_handle(model, executor)) is None


@pytest.mark.unit
def test_approval_interrupt_is_auto_rejected_and_the_run_continues(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    make_executor: Callable[..., Any],
) -> None:
    investigate = _investigate()

    executor = make_executor(estimated_rows=BIG)
    model = scripted_model(
        tool_call_message("task", TASK, "t1"),
        tool_call_message("run_sql", {"sql": "select * from huge", "purpose": "p"}, "q1"),
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="final report"),
    )

    chain = investigate(QUESTION, handle=_handle(model, executor))

    assert executor.run_calls == []  # the expensive query never ran
    assert chain is not None
    assert chain.root_cause is not None


@pytest.mark.unit
def test_parallel_interrupts_are_all_rejected(
    scripted_model: Callable[..., Any], make_executor: Callable[..., Any]
) -> None:
    investigate = _investigate()

    executor = make_executor(estimated_rows=BIG)
    two_calls = AIMessage(
        content="",
        tool_calls=[
            {"name": "run_sql", "args": {"sql": f"select {i}", "purpose": "p"}, "id": f"q{i}"}
            for i in range(2)
        ],
    )
    model = scripted_model(
        AIMessage(content="", tool_calls=[{"name": "task", "args": TASK, "id": "t1"}]),
        two_calls,
        AIMessage(content="", tool_calls=[{"name": "WhyChain", "args": CHAIN, "id": "s1"}]),
        AIMessage(content="final report"),
    )

    chain = investigate(QUESTION, handle=_handle(model, executor))

    assert executor.run_calls == []
    assert chain is not None


class _SpyGraph:
    """Delegates to the real graph and records the config of every invoke."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self.configs: list[dict[str, Any]] = []

    def invoke(self, payload: Any, config: Any = None, **kwargs: Any) -> Any:
        self.configs.append(dict(config or {}))
        return self._graph.invoke(payload, config, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


@pytest.mark.unit
def test_runs_on_a_fresh_thread_with_the_step_budget_from_settings(
    scripted_model: Callable[..., Any], executor: Any
) -> None:
    investigate = _investigate()

    settings = _settings(investigation_max_steps=7)
    handles = []
    for _ in range(2):
        model = scripted_model(AIMessage(content="no chain"))
        real = _handle(model, executor, settings)
        handles.append(dataclasses.replace(real, graph=_SpyGraph(real.graph)))

    for handle in handles:
        investigate(QUESTION, handle=handle)

    configs = [c for h in handles for c in h.graph.configs]
    assert len(configs) == 2
    assert {c["recursion_limit"] for c in configs} == {7}
    thread_ids = {c["configurable"]["thread_id"] for c in configs}
    assert len(thread_ids) == 2 and all(thread_ids)


@pytest.mark.unit
def test_investigation_max_steps_defaults_to_80_and_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _settings().model_dump()["investigation_max_steps"] == 80

    monkeypatch.setenv("ROOTCAUSE_INVESTIGATION_MAX_STEPS", "25")
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    assert Settings(_env_file=None).model_dump()["investigation_max_steps"] == 25


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, -1])
def test_investigation_max_steps_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        _settings(investigation_max_steps=value)


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR)")
    con.execute("INSERT INTO plants VALUES ('P01')")
    con.close()
    return path


@pytest.fixture
def configured_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, warehouse: Path
) -> pytest.MonkeyPatch:
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "test")
    monkeypatch.setenv("ROOTCAUSE_WAREHOUSE_PATH", str(warehouse))
    return monkeypatch


@pytest.mark.unit
def test_builds_from_settings_with_the_warehouse_executor(
    configured_env: pytest.MonkeyPatch,
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
) -> None:
    import rootcause.agents.orchestrator as orchestrator

    investigate = _investigate()

    model = scripted_model(
        tool_call_message("task", TASK, "t1"),
        tool_call_message("run_sql", {"sql": "select count(*) from plants", "purpose": "p"}, "q1"),
        tool_call_message("WhyChain", CHAIN, "s1"),
        AIMessage(content="final report"),
    )
    configured_env.setattr(orchestrator, "chat_model", lambda role, settings: model)

    chain = investigate(QUESTION)

    assert chain is not None
    assert chain.root_cause is not None
    assert chain.root_cause.statement == "no SOP for die change"


@pytest.mark.unit
def test_missing_warehouse_settings_raise_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    investigate = _investigate()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "test")

    with pytest.raises(ConfigurationError, match="WAREHOUSE"):
        investigate(QUESTION)


@pytest.mark.unit
def test_warehouse_path_to_a_missing_file_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    investigate = _investigate()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "test")
    monkeypatch.setenv("ROOTCAUSE_WAREHOUSE_PATH", str(tmp_path / "absent.duckdb"))

    with pytest.raises(ConfigurationError):
        investigate(QUESTION)
