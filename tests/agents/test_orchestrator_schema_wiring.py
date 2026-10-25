"""build_orchestrator wires schema tools only when the executor can introspect (RED)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import Field

from rootcause.settings import Settings

SQL_TOOLS = {"run_sql", "explain_sql"}
SCHEMA_TOOLS = {"list_tables", "describe_table", "column_values"}


def _settings() -> Settings:
    return Settings(
        _env_file=None, database_url="postgresql://u:p@localhost/db", environment="test"
    )


class _RecordingModel(FakeMessagesListChatModel):
    """Scripted model that records the tool names the agent binds to it."""

    bound: list[set[str]] = Field(default_factory=list)

    def bind_tools(  # type: ignore[override]
        self, tools: Sequence[Any], **kwargs: Any
    ) -> Runnable[Any, BaseMessage]:
        self.bound.append({str(getattr(t, "name", None) or t["name"]) for t in tools})
        return self


def _model(scripted_model: Callable[..., Any]) -> _RecordingModel:
    return _RecordingModel(responses=[AIMessage(content="done")])


def _tool_names(model: _RecordingModel, executor: Any, table_docs: Any = None) -> set[str]:
    from rootcause.agents.orchestrator import build_orchestrator

    kwargs: dict[str, Any] = {"executor": executor}
    if table_docs is not None:
        kwargs["table_docs"] = table_docs
    handle = build_orchestrator(
        model=model,
        settings=_settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        **kwargs,
    )
    handle.graph.invoke(
        {"messages": [HumanMessage("hi")]}, config={"configurable": {"thread_id": "t"}}
    )
    return set(model.bound[-1])


@pytest.fixture
def duck_executor(tmp_path: Path) -> Any:
    from rootcause.connections.duckdb_executor import (  # type: ignore[import-not-found]
        DuckDBExecutor,
    )

    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR)")
    con.execute("INSERT INTO plants VALUES ('P01')")
    con.close()
    with DuckDBExecutor(path) as ex:
        yield ex


@pytest.mark.unit
def test_schema_capable_executor_exposes_sql_and_schema_tools(
    scripted_model: Callable[..., Any], duck_executor: Any
) -> None:
    from rootcause.evals.manufacturing.table_docs import TABLE_DOCS

    names = _tool_names(_model(scripted_model), duck_executor, TABLE_DOCS)
    assert names >= (SQL_TOOLS | SCHEMA_TOOLS)


@pytest.mark.unit
def test_schema_tools_work_without_table_docs(
    scripted_model: Callable[..., Any], duck_executor: Any
) -> None:
    names = _tool_names(_model(scripted_model), duck_executor)
    assert names >= (SQL_TOOLS | SCHEMA_TOOLS)


@pytest.mark.unit
def test_sql_only_executor_gets_no_schema_tools(
    scripted_model: Callable[..., Any], executor: Any
) -> None:
    names = _tool_names(_model(scripted_model), executor)
    assert names >= SQL_TOOLS
    assert not (SCHEMA_TOOLS & names)


@pytest.mark.unit
def test_schema_tool_call_runs_end_to_end(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    duck_executor: Any,
) -> None:
    from rootcause.agents.orchestrator import build_orchestrator

    model = scripted_model(tool_call_message("list_tables", {}, "c1"), AIMessage(content="done"))
    handle = build_orchestrator(
        model=model,
        settings=_settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=duck_executor,
    )
    result = handle.graph.invoke(
        {"messages": [HumanMessage("what tables?")]}, config={"configurable": {"thread_id": "t"}}
    )
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert any("plants" in str(m.content) for m in tool_messages)
