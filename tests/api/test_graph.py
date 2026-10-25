"""langgraph.json registers the real "rootcause" graph factory (replaces the echo placeholder)."""

import importlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import Runnable
from pydantic import Field

REPO_ROOT = Path(__file__).resolve().parents[2]
LANGGRAPH_JSON = REPO_ROOT / "langgraph.json"
GRAPH_NAME = "rootcause"


class _RecordingModel(FakeMessagesListChatModel):
    bound: list[set[str]] = Field(default_factory=list)

    def bind_tools(  # type: ignore[override]
        self, tools: Sequence[Any], **kwargs: Any
    ) -> Runnable[Any, BaseMessage]:
        self.bound.append({str(getattr(t, "name", None) or t["name"]) for t in tools})
        return self


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> _RecordingModel:
    import rootcause.agents.orchestrator as orchestrator

    recording = _RecordingModel(responses=[AIMessage(content="done")])
    monkeypatch.setattr(orchestrator, "chat_model", lambda role, settings: recording)
    return recording


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "test")
    return monkeypatch


def _make_graph() -> Any:
    return importlib.import_module("rootcause.api.graphs").make_graph


def _graph_spec() -> str:
    return str(json.loads(LANGGRAPH_JSON.read_text())["graphs"][GRAPH_NAME])


@pytest.mark.unit
def test_langgraph_json_exists_and_registers_only_the_rootcause_graph() -> None:
    config = json.loads(LANGGRAPH_JSON.read_text())

    assert isinstance(config["graphs"], dict)
    assert GRAPH_NAME in config["graphs"]
    assert "placeholder" not in config["graphs"]


@pytest.mark.unit
def test_registered_spec_points_at_the_make_graph_factory() -> None:
    file_part, _, attribute = _graph_spec().partition(":")

    target = (REPO_ROOT / file_part).resolve()

    assert target.is_file()
    assert target == (REPO_ROOT / "src/rootcause/api/graphs.py").resolve()
    assert attribute == "make_graph"
    assert f"def {attribute}" in target.read_text()


@pytest.mark.unit
def test_langgraph_dependencies_include_the_rootcause_package() -> None:
    dependencies = json.loads(LANGGRAPH_JSON.read_text())["dependencies"]

    assert dependencies == ["."]


@pytest.mark.unit
def test_importing_the_module_builds_nothing_and_needs_no_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    module = importlib.reload(importlib.import_module("rootcause.api.graphs"))

    assert callable(module.make_graph)
    assert not hasattr(module, "graph")


@pytest.mark.unit
def test_make_graph_returns_a_compiled_graph_without_a_checkpointer(
    base_env: pytest.MonkeyPatch, model: _RecordingModel
) -> None:
    make_graph = _make_graph()

    graph = make_graph()

    assert type(graph).__name__ == "CompiledStateGraph"
    assert graph.checkpointer is None  # LangGraph Server injects its own


@pytest.mark.unit
def test_make_graph_without_a_warehouse_builds_without_sql_tools(
    base_env: pytest.MonkeyPatch, model: _RecordingModel
) -> None:
    make_graph = _make_graph()

    make_graph().invoke({"messages": [HumanMessage("hi")]})

    assert model.bound
    assert not ({"run_sql", "explain_sql", "list_tables"} & model.bound[-1])


@pytest.mark.unit
def test_make_graph_with_a_warehouse_has_sql_and_schema_tools(
    base_env: pytest.MonkeyPatch, model: _RecordingModel, tmp_path: Path
) -> None:
    make_graph = _make_graph()

    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR)")
    con.close()
    base_env.setenv("ROOTCAUSE_WAREHOUSE_PATH", str(path))

    graph = make_graph()
    graph.invoke({"messages": [HumanMessage("hi")]})

    assert {"run_sql", "explain_sql", "list_tables", "describe_table"} <= model.bound[-1]
    assert graph.checkpointer is None
