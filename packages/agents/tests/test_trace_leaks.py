"""T009 amendment: no customer data or credential reaches Langfuse spans or logs.

End to end: the real orchestrator (DeepAgents filesystem tools and eviction included)
runs against a scripted model with sentinel values in query rows, tool output, error
messages and the model's own answer; every span the exporter receives (attributes,
status description, events, resource) and every log line is scanned for the sentinels.
"""

import io
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit

PASSWORD = "PWSENTINEL-9f8e"
DSN = f"postgresql://svc:{PASSWORD}@db.internal:5432/rc"
ANTHROPIC_KEY = "sk-ant-api03-LLMKEYSENTINEL"
SECRET_KEY = "sk-lf-SECSENTINEL"
EMAIL = "PII-EMAIL-jane@example.com"
ROW = "PIIROW-Ayse-Yilmaz"
CSV = "PIICSV-Mehmet-Demir"
RECORD = "PIIRECORD-Zeynep-Kaya"
TOOL_ERROR = "PIITOOLERR-Can-Ozturk"
EXEC_ERROR = "PIIEXECERR-Deniz-Arslan"
BIG = "PIIBIG-Selin-Kaya"
QUERY = {"sql": "SELECT email FROM users", "purpose": "probe"}


@dataclass
class RowsExecutor:
    """Returns sentinel rows (bytes, Decimal, nested values), many of them, or fails."""

    mode: str = "small"

    def explain(self, sql: str) -> Any:
        from rootcause_agents.tools.sql import QueryEstimate

        return QueryEstimate(estimated_rows=1, estimated_cost=1.0)

    def run(self, sql: str, purpose: str) -> Any:
        from rootcause_agents.tools.sql import QueryResult

        if self.mode == "raise":
            raise RuntimeError(f"row ('{EXEC_ERROR}',) violates constraint; dsn={DSN}")
        rows: tuple[tuple[Any, ...], ...]
        if self.mode == "big":
            rows = tuple((f"{ROW}-{i}", b"x", Decimal(i), [str(i)]) for i in range(20_000))
        else:
            rows = ((EMAIL, ROW.encode(), Decimal("4242.4242"), [[ROW]]),)
        return QueryResult(
            columns=("email", "b", "d", "n"),
            rows=rows,
            row_count=len(rows),
            truncated=False,
            sql=sql,
        )


@tool
def export_csv(table: str) -> str:
    """Return CSV text of a table."""
    return f"email,amount\n{CSV},12\n"


@tool
def profile_table(table: str) -> dict[str, Any]:
    """Return a profile with sample records."""
    return {"records": [{"email": RECORD}], "sample": [[RECORD, 1]], "dsn": DSN, "k": ANTHROPIC_KEY}


@tool
def broken_tool(table: str) -> str:
    """Always fails."""
    raise ValueError(f"cannot read row {TOOL_ERROR} via {DSN} key {ANTHROPIC_KEY}")


@tool
def dump_everything(table: str) -> str:
    """Return a very large text (DeepAgents evicts it to /large_tool_results/)."""
    return f"{BIG} " * 20_000


@dataclass
class Outcome:
    spans: list[Any]
    logs: str
    state: dict[str, Any]
    settings: Settings


@pytest.fixture
def log_text(capsys: pytest.CaptureFixture[str]) -> Iterator[Callable[[], str]]:
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    def read() -> str:
        captured = capsys.readouterr()
        return buffer.getvalue() + captured.out + captured.err

    yield read
    root.removeHandler(handler)
    root.setLevel(previous)


@pytest.fixture
def run(
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Settings],
    scripted_model: Callable[..., Any],
    log_text: Callable[[], str],
) -> Callable[..., Outcome]:
    from rootcause_agents.orchestrator import build_orchestrator
    from rootcause_core.clients.langfuse import flush_langfuse
    from rootcause_core.logging import configure_logging

    def go(
        script: list[AIMessage],
        *,
        mode: str = "small",
        tools: tuple[Any, ...] = (),
        human: str = "why did orders drop?",
        **config_kwargs: Any,
    ) -> Outcome:
        settings = make_langfuse_settings(
            database_url=DSN,
            anthropic_api_key=ANTHROPIC_KEY,
            langfuse_secret_key=SECRET_KEY,
        )
        configure_logging(settings)
        handle = build_orchestrator(
            model=scripted_model(*script),
            settings=settings,
            store=InMemoryStore(),
            checkpointer=InMemorySaver(),
            executor=RowsExecutor(mode),
            extra_tools=tools,
        )
        config = handle.config_for("thread-1", user_id="u1", **config_kwargs)
        try:
            handle.graph.invoke({"messages": [HumanMessage(human)]}, config=config)
        except Exception as error:  # noqa: BLE001 - a failing tool may abort the run
            logging.getLogger(__name__).info("run aborted: %s", type(error).__name__)
        flush_langfuse()
        state = handle.graph.get_state(handle.config_for("thread-1")).values
        return Outcome(
            list(langfuse_capture.exporter.get_finished_spans()), log_text(), state, settings
        )

    return go


def _needles(settings: Settings, *extra: str) -> dict[str, str]:
    assert settings.langfuse_public_key is not None
    return {
        "email": EMAIL,
        "row": ROW,
        "password": PASSWORD,
        "anthropic key": ANTHROPIC_KEY,
        "langfuse secret": SECRET_KEY,
        "langfuse public": settings.langfuse_public_key.get_secret_value(),
        **{value: value for value in extra},
    }


def _blobs(span: Any) -> Iterator[tuple[str, str]]:
    for key, value in (span.attributes or {}).items():
        yield f"attribute {key}", json.dumps(value, default=str)
    yield "status description", str(span.status.description)
    for event in span.events:
        yield f"event {event.name}", json.dumps(dict(event.attributes or {}), default=str)
    yield "resource", json.dumps(dict(span.resource.attributes), default=str)


def _leaks(outcome: Outcome, *extra: str) -> list[str]:
    needles = _needles(outcome.settings, *extra)
    found: list[str] = []
    for span in outcome.spans:
        for where, blob in _blobs(span):
            found += [
                f"{label} in {span.name}: {where}" for label, n in needles.items() if n in blob
            ]
    found += [f"{label} in logs" for label, n in needles.items() if n in outcome.logs]
    return found


def _call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _run_sql_span(outcome: Outcome) -> Any:
    return next(span for span in outcome.spans if span.name == "run_sql")


# --- (1a, b) run_sql results ---


@pytest.mark.parametrize("mode", ["small", "big"], ids=["small-result", "20k-row-result"])
def test_run_sql_rows_never_reach_spans_or_logs(run: Callable[..., Outcome], mode: str) -> None:
    outcome = run([_call("run_sql", QUERY, "c1"), AIMessage(content="done")], mode=mode)

    assert outcome.spans
    assert _leaks(outcome, "PIIROW-Ayse-Yilmaz-19999") == []


@pytest.mark.parametrize("mode", ["small", "big"], ids=["small-result", "20k-row-result"])
def test_run_sql_span_keeps_columns_row_count_and_sql(
    run: Callable[..., Outcome], mode: str
) -> None:
    outcome = run([_call("run_sql", QUERY, "c1"), AIMessage(content="done")], mode=mode)

    span = _run_sql_span(outcome)
    output = str(span.attributes["langfuse.observation.output"])
    assert "email" in output
    assert "row_count" in output
    assert ("1" if mode == "small" else "20000") in output
    assert "SELECT email FROM users" in output + str(
        span.attributes.get("langfuse.observation.input")
    )


# --- (1c) reading the stored rows back ---


def test_reading_the_stored_query_result_does_not_leak_it(run: Callable[..., Outcome]) -> None:
    script = [
        _call("run_sql", QUERY, "c1"),
        _call("read_file", {"file_path": "/query_results/c1.json"}, "c2"),
        _call("grep", {"pattern": "PIIROW", "path": "/query_results/"}, "c3"),
        _call("ls", {"path": "/query_results/"}, "c4"),
        AIMessage(content="done"),
    ]

    outcome = run(script, mode="big")

    assert _leaks(outcome) == []
    names = {span.name for span in outcome.spans}
    assert {"run_sql", "read_file"} <= names


def test_reading_an_evicted_large_tool_result_does_not_leak_it(
    run: Callable[..., Outcome],
) -> None:
    script = [
        _call("dump_everything", {"table": "t"}, "c1"),
        _call("read_file", {"file_path": "/large_tool_results/c1"}, "c2"),
        _call("grep", {"pattern": "PIIBIG", "path": "/large_tool_results/"}, "c3"),
        AIMessage(content="done"),
    ]

    outcome = run(script, tools=(dump_everything,))

    assert _leaks(outcome, BIG) == []


def test_the_eviction_preview_of_run_sql_is_not_needed_because_rows_never_enter_messages(
    run: Callable[..., Outcome],
) -> None:
    outcome = run([_call("run_sql", QUERY, "c1"), AIMessage(content="done")], mode="big")

    messages = outcome.state["messages"]
    tool_message = next(m for m in messages if isinstance(m, ToolMessage))
    assert ROW not in str(tool_message.content)
    assert "/large_tool_results/" not in str(tool_message.content)


# --- (1d) failures ---


def test_a_tool_that_raises_does_not_leak_dsn_key_or_row_values(
    run: Callable[..., Outcome],
) -> None:
    script = [_call("broken_tool", {"table": "t"}, "c1"), AIMessage(content="done")]

    outcome = run(script, tools=(broken_tool,))

    assert outcome.spans
    assert _leaks(outcome, TOOL_ERROR) == []


def test_an_executor_that_raises_does_not_leak_dsn_or_row_values(
    run: Callable[..., Outcome],
) -> None:
    outcome = run([_call("run_sql", QUERY, "c1"), AIMessage(content="done")], mode="raise")

    assert outcome.spans
    assert _leaks(outcome, EXEC_ERROR) == []


# --- (1e) other tools' output ---


def test_csv_text_and_rows_under_records_from_other_tools_do_not_leak(
    run: Callable[..., Outcome],
) -> None:
    script = [
        _call("export_csv", {"table": "u"}, "c1"),
        _call("profile_table", {"table": "u"}, "c2"),
        AIMessage(content="done"),
    ]

    outcome = run(script, tools=(export_csv, profile_table))

    assert _leaks(outcome, CSV, RECORD) == []
    tools = {span.name for span in outcome.spans}
    assert {"export_csv", "profile_table"} <= tools


def test_unlisted_tool_spans_carry_the_redaction_marker(run: Callable[..., Outcome]) -> None:
    script = [_call("export_csv", {"table": "u"}, "c1"), AIMessage(content="done")]

    outcome = run(script, tools=(export_csv,))

    span = next(span for span in outcome.spans if span.name == "export_csv")
    assert "<redacted tool output: export_csv>" in str(
        span.attributes["langfuse.observation.output"]
    )


def test_tool_output_echoed_in_the_next_generation_input_is_redacted(
    run: Callable[..., Outcome],
) -> None:
    script = [_call("export_csv", {"table": "u"}, "c1"), AIMessage(content="done")]

    outcome = run(script, tools=(export_csv,))

    generations = [
        span
        for span in outcome.spans
        if span.attributes.get("langfuse.observation.type") == "generation"
    ]
    assert len(generations) == 2
    assert CSV not in str(generations[-1].attributes.get("langfuse.observation.input"))


def test_write_todos_output_stays_visible(run: Callable[..., Outcome]) -> None:
    todos = [{"content": "Frame the problem", "status": "in_progress"}]
    script = [_call("write_todos", {"todos": todos}, "c1"), AIMessage(content="done")]

    outcome = run(script)

    span = next(span for span in outcome.spans if span.name == "write_todos")
    assert "<redacted tool output" not in str(span.attributes.get("langfuse.observation.output"))


# --- (1f) the model's own answer ---


def test_an_email_echoed_in_the_final_answer_is_masked(run: Callable[..., Outcome]) -> None:
    script = [_call("run_sql", QUERY, "c1"), AIMessage(content=f"The top user is {EMAIL}.")]

    outcome = run(script)

    assert _leaks(outcome) == []
    generations = [
        span
        for span in outcome.spans
        if span.attributes.get("langfuse.observation.type") == "generation"
    ]
    assert "The top user is" in str(generations[-1].attributes["langfuse.observation.output"])


def test_an_email_typed_by_the_user_is_masked(run: Callable[..., Outcome]) -> None:
    outcome = run([AIMessage(content="ok")], human=f"look up {EMAIL} please")

    assert _leaks(outcome) == []


def test_a_dsn_pasted_by_the_user_is_masked(run: Callable[..., Outcome]) -> None:
    outcome = run([AIMessage(content="ok")], human=f"my database is {DSN}")

    assert _leaks(outcome) == []


# --- (2) run_sql contract inside the graph ---


def test_run_sql_message_is_a_summary_and_rows_live_in_agent_state(
    run: Callable[..., Outcome],
) -> None:
    outcome = run([_call("run_sql", QUERY, "c1"), AIMessage(content="done")])

    tool_message = next(m for m in outcome.state["messages"] if isinstance(m, ToolMessage))
    summary = json.loads(str(tool_message.content))
    assert {"columns", "row_count", "truncated", "sql"} <= set(summary)
    assert "rows" not in summary
    assert "/query_results/c1.json" in str(tool_message.content)
    assert ROW not in str(tool_message.content)
    assert EMAIL not in str(tool_message.content)
    stored = json.dumps(outcome.state["files"]["/query_results/c1.json"])
    assert EMAIL in stored
    assert ROW in stored


def test_stored_rows_can_be_read_back_by_the_agent(run: Callable[..., Outcome]) -> None:
    script = [
        _call("run_sql", QUERY, "c1"),
        _call("read_file", {"file_path": "/query_results/c1.json"}, "c2"),
        AIMessage(content="done"),
    ]

    outcome = run(script)

    read = [m for m in outcome.state["messages"] if isinstance(m, ToolMessage)][-1]
    assert EMAIL in str(read.content)


def test_each_run_sql_call_gets_its_own_result_file(run: Callable[..., Outcome]) -> None:
    script = [_call("run_sql", QUERY, "c1"), _call("run_sql", QUERY, "c2"), AIMessage(content="d")]

    outcome = run(script)

    assert {"/query_results/c1.json", "/query_results/c2.json"} <= set(outcome.state["files"])


# --- (7) isolation ---


@pytest.fixture
def foreign_span() -> Iterator[Any]:
    """An unrelated, valid OpenTelemetry span active while the agent runs (FastAPI's span)."""
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    with provider.get_tracer("fastapi").start_as_current_span("POST /ask") as span:
        yield span
    provider.shutdown()


def test_a_foreign_active_span_does_not_become_the_langfuse_parent(
    run: Callable[..., Outcome], foreign_span: Any
) -> None:
    outcome = run([_call("run_sql", QUERY, "c1"), AIMessage(content="done")])

    foreign_trace = foreign_span.get_span_context().trace_id
    foreign_id = foreign_span.get_span_context().span_id
    roots = [span for span in outcome.spans if span.parent is None]
    assert len(roots) == 1
    assert {span.context.trace_id for span in outcome.spans}.isdisjoint({foreign_trace})
    assert all(span.parent is None or span.parent.span_id != foreign_id for span in outcome.spans)


def test_every_run_gets_a_fresh_trace_id_even_under_the_same_foreign_span(
    run: Callable[..., Outcome], foreign_span: Any, langfuse_capture: Any
) -> None:
    first = run([AIMessage(content="one")])
    langfuse_capture.exporter.clear()
    second = run([AIMessage(content="two")])

    first_ids = {span.context.trace_id for span in first.spans}
    second_ids = {span.context.trace_id for span in second.spans}
    assert len(first_ids) == len(second_ids) == 1
    assert first_ids != second_ids


def test_trace_metadata_carries_tenant_and_connection(run: Callable[..., Outcome]) -> None:
    outcome = run([AIMessage(content="ok")], tenant_id="tenant-7", connection_id="conn-3")

    root = next(span for span in outcome.spans if span.parent is None)
    values = {key.rsplit(".", 1)[-1]: value for key, value in root.attributes.items()}
    assert values["tenant_id"] == "tenant-7"
    assert values["connection_id"] == "conn-3"
