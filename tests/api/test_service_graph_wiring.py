"""make_service_graph(wiring=...) builds the orchestrator over a connection's catalog (RED).

With ``wiring`` (a ``rootcause.catalog.agent_tools.CatalogWiring``) the graph uses
``wiring.executor``, ``wiring.table_docs`` and ``wiring.tools`` (``search_catalog``)
instead of the ``ROOTCAUSE_WAREHOUSE_PATH`` executor and the demo ``TABLE_DOCS``;
the env warehouse is not opened.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver

pytestmark = pytest.mark.unit


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[Any, Any]:
        return self


@tool
def search_catalog(query: str, k: int = 5) -> dict[str, Any]:
    """Find catalog tables and columns similar to ``query``."""
    return {"results": []}


@dataclass(frozen=True)
class _Doc:
    description: str
    columns: dict[str, str]


@dataclass(frozen=True)
class _Wiring:
    executor: Any
    table_docs: dict[str, _Doc]
    tools: tuple[BaseTool, ...]


@pytest.fixture
def graphs(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[Any]:
    import rootcause.agents.orchestrator as orchestrator
    import rootcause.api.graphs as graphs_module
    from rootcause.settings import clear_settings_cache

    monkeypatch.chdir(tmp_path)
    for name in [key for key in os.environ if key.startswith("ROOTCAUSE_")]:
        monkeypatch.delenv(name)
    clear_settings_cache()
    monkeypatch.setattr(graphs_module, "_executors", {})
    model = _Model(responses=[AIMessage(content="hello back")])
    monkeypatch.setattr(orchestrator, "chat_model", lambda role, settings: model)
    yield graphs_module
    clear_settings_cache()


@pytest.fixture
def spy(graphs: Any, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    real = graphs.build_orchestrator

    def wrapper(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(graphs, "build_orchestrator", wrapper)
    return calls


def test_wiring_replaces_the_env_warehouse_executor_docs_and_adds_its_tools(
    graphs: Any, spy: list[dict[str, Any]], executor: Any
) -> None:
    from rootcause.settings import Settings

    settings = Settings(database_url="postgresql://u:p@127.0.0.1:1/db", environment="test")
    docs = {"plants": _Doc("Sites.", {"plant_id": "Id."})}
    wiring = _Wiring(executor=executor, table_docs=docs, tools=(search_catalog,))
    saver = InMemorySaver()

    graph = graphs.make_service_graph(checkpointer=saver, settings=settings, wiring=wiring)

    assert graph.checkpointer is saver
    [call] = spy
    assert call["executor"] is executor
    assert call["table_docs"] == docs
    assert [t.name for t in call["extra_tools"]] == ["search_catalog"]
    assert graphs._executors == {}  # the env warehouse was never opened


@pytest.fixture
def executor() -> Any:
    from tests.agents.conftest import FakeExecutor

    return FakeExecutor()
