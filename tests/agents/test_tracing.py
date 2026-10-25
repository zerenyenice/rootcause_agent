"""T009: orchestrator runs are traced in Langfuse (one trace per thread, no network)."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause.settings import Settings

pytestmark = pytest.mark.unit

ROW_VALUE = "ROWVALUE-jane.doe@example.com"
TODOS = [{"content": "Frame the problem", "status": "in_progress"}]
FIRST = {"messages": [HumanMessage("orders dropped 18% last Tuesday, why?")]}


@dataclass
class SentinelExecutor:
    """Returns one row carrying a personal-data looking value."""

    explain_calls: list[str] = field(default_factory=list)

    def explain(self, sql: str) -> Any:
        from rootcause.agents.tools.sql import QueryEstimate

        self.explain_calls.append(sql)
        return QueryEstimate(estimated_rows=1, estimated_cost=1.0)

    def run(self, sql: str, purpose: str) -> Any:
        from rootcause.agents.tools.sql import QueryResult

        return QueryResult(
            columns=("email",), rows=((ROW_VALUE,),), row_count=1, truncated=False, sql=sql
        )


def _build(
    settings: Settings,
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
) -> Any:
    from rootcause.agents.orchestrator import build_orchestrator

    model = scripted_model(
        tool_call_message("write_todos", {"todos": TODOS}, "c1"),
        tool_call_message("run_sql", {"sql": "SELECT email FROM users", "purpose": "probe"}, "c2"),
        AIMessage(content="done"),
    )
    return build_orchestrator(
        model=model,
        settings=settings,
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=SentinelExecutor(),
    )


def _spans(capture: Any) -> list[Any]:
    from rootcause.clients.langfuse import flush_langfuse

    flush_langfuse()
    return list(capture.exporter.get_finished_spans())


def _kind(span: Any) -> str | None:
    return span.attributes.get("langfuse.observation.type")  # type: ignore[no-any-return]


def _run(handle: Any, thread: str, **kwargs: Any) -> None:
    handle.graph.invoke(FIRST, config=handle.config_for(thread, **kwargs))


def test_config_for_without_langfuse_is_just_the_thread(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    make_langfuse_settings: Callable[..., Settings],
) -> None:
    handle = _build(
        make_langfuse_settings(langfuse_enabled=False), scripted_model, tool_call_message
    )

    config = handle.config_for("t1")

    assert config["configurable"]["thread_id"] == "t1"
    assert not config.get("callbacks")


def test_disabled_langfuse_leaves_the_run_unchanged(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    make_langfuse_settings: Callable[..., Settings],
    langfuse_capture: Any,
) -> None:
    handle = _build(
        make_langfuse_settings(langfuse_enabled=False), scripted_model, tool_call_message
    )

    _run(handle, "t1")

    todos = handle.graph.get_state(handle.config_for("t1")).values["todos"]
    assert [t["content"] for t in todos] == ["Frame the problem"]
    assert langfuse_capture.init_kwargs == []
    assert langfuse_capture.exporter.get_finished_spans() == ()


def test_config_for_uses_the_settings_given_to_the_orchestrator(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    make_langfuse_settings: Callable[..., Settings],
    langfuse_capture: Any,
    langfuse_env: Settings,
) -> None:
    from langfuse.langchain import CallbackHandler

    handle = _build(langfuse_env, scripted_model, tool_call_message)

    config = handle.config_for("thread-9", user_id="analyst-1", tags=("rootcause",))

    assert config["configurable"]["thread_id"] == "thread-9"
    assert [type(c) for c in config["callbacks"]] == [CallbackHandler]
    assert config["metadata"]["langfuse_session_id"] == "thread-9"
    assert config["metadata"]["langfuse_user_id"] == "analyst-1"
    assert config["metadata"]["langfuse_tags"] == ["rootcause"]


def test_one_trace_holds_model_todo_and_tool_observations(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    langfuse_capture: Any,
    langfuse_env: Settings,
) -> None:
    handle = _build(langfuse_env, scripted_model, tool_call_message)

    _run(handle, "thread-e2e")

    spans = _spans(langfuse_capture)
    assert spans, "no spans were exported"
    assert len({span.context.trace_id for span in spans}) == 1
    roots = [span for span in spans if span.parent is None]
    assert len(roots) == 1
    generations = [span for span in spans if _kind(span) == "generation"]
    assert len(generations) == 3  # one model call per scripted message
    tools = {span.name: span for span in spans if _kind(span) == "tool"}
    assert {"write_todos", "run_sql"} <= set(tools)
    for span in (*roots, *generations, *tools.values()):
        assert span.attributes["session.id"] == "thread-e2e"


def test_user_and_tags_are_recorded_on_the_trace(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    langfuse_capture: Any,
    langfuse_env: Settings,
) -> None:
    handle = _build(langfuse_env, scripted_model, tool_call_message)

    _run(handle, "t1", user_id="analyst-7", tags=("rootcause", "e2e"))

    [root] = [span for span in _spans(langfuse_capture) if span.parent is None]
    assert root.attributes["user.id"] == "analyst-7"
    assert set(root.attributes["langfuse.trace.tags"]) == {"rootcause", "e2e"}


def test_each_thread_gets_its_own_trace_and_session(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    langfuse_capture: Any,
    langfuse_env: Settings,
) -> None:
    first = _build(langfuse_env, scripted_model, tool_call_message)
    second = _build(langfuse_env, scripted_model, tool_call_message)

    _run(first, "thread-a")
    _run(second, "thread-b")

    roots = [span for span in _spans(langfuse_capture) if span.parent is None]
    assert len(roots) == 2
    assert len({root.context.trace_id for root in roots}) == 2
    assert {root.attributes["session.id"] for root in roots} == {"thread-a", "thread-b"}


def test_traces_carry_no_raw_sql_rows_but_keep_columns_and_counts(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    langfuse_capture: Any,
    langfuse_env: Settings,
) -> None:
    handle = _build(langfuse_env, scripted_model, tool_call_message)

    _run(handle, "t1")

    spans = _spans(langfuse_capture)
    for span in spans:
        for key, value in span.attributes.items():
            assert ROW_VALUE not in json.dumps(value, default=str), (span.name, key)
    run_sql = next(span for span in spans if span.name == "run_sql")
    output = str(run_sql.attributes["langfuse.observation.output"])
    assert "email" in output
    assert "row_count" in output


def test_credentials_in_agent_input_are_redacted_in_the_trace(
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
    langfuse_capture: Any,
    langfuse_env: Settings,
) -> None:
    handle = _build(langfuse_env, scripted_model, tool_call_message)
    leaky = {"messages": [HumanMessage("use postgresql://u:hunter2@h/db to look")]}

    handle.graph.invoke(leaky, config=handle.config_for("t1"))

    for span in _spans(langfuse_capture):
        for value in span.attributes.values():
            assert "hunter2" not in json.dumps(value, default=str)
