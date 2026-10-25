"""make_graph() opens the warehouse once per process, not once per call (RED)."""

from pathlib import Path
from typing import Any

import duckdb
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from tests.agents.conftest import FakeExecutor


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[Any, Any]:
        return self


@pytest.mark.unit
def test_make_graph_called_twice_opens_the_warehouse_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import rootcause.agents.orchestrator as orchestrator
    import rootcause.api.graphs as graphs
    from rootcause.settings import clear_settings_cache

    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR)")
    con.close()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "test")
    monkeypatch.setenv("ROOTCAUSE_WAREHOUSE_PATH", str(path))
    clear_settings_cache()
    model = _Model(responses=[AIMessage(content="done")])
    monkeypatch.setattr(orchestrator, "chat_model", lambda role, settings: model)
    opened: list[Any] = []

    def spy(settings: Any) -> Any:
        opened.append(settings)
        return FakeExecutor()

    monkeypatch.setattr(graphs, "executor_from_settings", spy, raising=True)

    graphs.make_graph()
    graphs.make_graph()

    assert len(opened) == 1
