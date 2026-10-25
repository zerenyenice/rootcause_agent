"""make_service_graph compiles the orchestrator with the caller's saver (no network)."""

import os
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver

pytestmark = pytest.mark.unit


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[Any, Any]:
        return self


@pytest.fixture
def graphs(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
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
def settings() -> Any:
    from rootcause.settings import Settings

    return Settings(database_url="postgresql://u:p@127.0.0.1:1/db", environment="test")


def test_builds_with_the_injected_checkpointer_and_persists_state(
    graphs: Any, settings: Any
) -> None:
    saver = InMemorySaver()
    graph = graphs.make_service_graph(checkpointer=saver, settings=settings)

    assert graph.checkpointer is saver
    config = {"configurable": {"thread_id": "t-1"}}
    result = graph.invoke({"messages": [("user", "hi")]}, config)

    assert result["messages"][-1].content == "hello back"
    assert saver.get_tuple(config) is not None


def test_uses_get_settings_when_none_are_passed(
    graphs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "test")
    saver = InMemorySaver()
    assert graphs.make_service_graph(checkpointer=saver).checkpointer is saver


def test_without_a_warehouse_it_shares_the_cached_no_executor(graphs: Any, settings: Any) -> None:
    graphs.make_service_graph(checkpointer=InMemorySaver(), settings=settings)
    graphs.make_service_graph(checkpointer=InMemorySaver(), settings=settings)
    assert list(graphs._executors.values()) == [None]
