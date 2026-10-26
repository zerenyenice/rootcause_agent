"""Invalid SQL is fed back to the model, not escalated to a human (RED).

Seams:
- the run_sql approval predicate (``orchestrator._approval_config``) skips the
  interrupt when ``explain`` raises an invalid-statement error: ``QueryRejected``,
  ``UnknownTable``, or ``QueryFailed`` whose ``error_type`` is ``ParserException``,
  ``BinderException`` or ``CatalogException``. Anything else still fails closed.
- the run_sql / explain_sql error payload keeps ``{"error": {"code", "message"}}``
  and adds ``codes`` (``QueryRejected``) or ``detail`` (the DuckDB message of the
  three invalid-statement types, at most 500 characters). No other error carries
  a ``detail``, so row values quoted by cast/constraint errors never leak.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from structlog.testing import capture_logs

from rootcause.connections.duckdb_executor import (
    DuckDBExecutor,
    QueryFailed,
    QueryRejected,
    QueryTimeout,
    UnknownTable,
)
from rootcause.settings import Settings

THRESHOLD = 5_000_000
MAX_DETAIL = 500
GENERIC_MESSAGE = "The query could not be executed."
PAYLOAD_KEYS = {"code", "message", "codes", "detail"}
SENTINEL_VALUE = "SENTINEL-row-value"

# Passes the read-only validator (it cannot see derived-table columns), fails in DuckDB's binder.
BINDER_UNKNOWN_COLUMN_SQL = "SELECT s.no_such_column FROM (SELECT plant_id FROM plants) AS s"
# Binder error whose DuckDB message lists candidate functions.
BINDER_CANDIDATES_SQL = "SELECT sum(plant_id) FROM plants"
# Binder error whose DuckDB message is longer than MAX_DETAIL (the alias is echoed back).
BINDER_LONG_SQL = f"SELECT sum(plant_id) AS {'a' * 120} FROM plants"
# Rejected by the validator before DuckDB sees it.
VALIDATOR_UNKNOWN_COLUMN_SQL = "SELECT no_such_column FROM plants"
# Explains fine, fails at run time with a ConversionException quoting the value.
CAST_SQL = f"SELECT CAST('{SENTINEL_VALUE}' AS INTEGER)"

INVALID_STATEMENT_ERRORS: list[Any] = [
    pytest.param(lambda: QueryRejected(["unknown_column"]), id="QueryRejected"),
    pytest.param(UnknownTable, id="UnknownTable"),
    pytest.param(lambda: QueryFailed("ParserException"), id="ParserException"),
    pytest.param(lambda: QueryFailed("BinderException"), id="BinderException"),
    pytest.param(lambda: QueryFailed("CatalogException"), id="CatalogException"),
]
FAIL_CLOSED_ERRORS: list[Any] = [
    pytest.param(lambda: QueryTimeout(1.0), id="QueryTimeout"),
    pytest.param(lambda: QueryFailed("MissingEstimate"), id="MissingEstimate"),
    pytest.param(lambda: QueryFailed("MalformedPlan"), id="MalformedPlan"),
    pytest.param(lambda: QueryFailed("ConversionException"), id="ConversionException"),
    pytest.param(lambda: QueryFailed("InvalidInputException"), id="InvalidInputException"),
    pytest.param(lambda: RuntimeError("conn reset"), id="RuntimeError"),
]


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR, plant_name VARCHAR)")
    con.execute("INSERT INTO plants VALUES ('P01', 'Alpha'), ('P02', 'Beta')")
    con.close()
    return path


@pytest.fixture
def real_executor(warehouse: Path) -> Iterator[DuckDBExecutor]:
    with DuckDBExecutor(warehouse) as ex:
        yield ex


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        environment="test",
        scan_row_threshold=THRESHOLD,
    )


def _tools(executor: Any) -> dict[str, Any]:
    from rootcause.agents.tools.sql import make_sql_tools

    return {tool.name: tool for tool in make_sql_tools(executor, THRESHOLD)}


def _invoke(tool: Any, sql: str) -> dict[str, Any]:
    """Invoke a SQL tool with a full ToolCall and return the model-visible payload."""
    args = {"sql": sql, "purpose": "p"} if tool.name == "run_sql" else {"sql": sql}
    out = tool.invoke(
        {"name": tool.name, "args": args, "id": "call-1", "type": "tool_call"},
        config={"configurable": {"thread_id": "t"}},
    )
    content = getattr(out, "content", out)
    payload = json.loads(content) if isinstance(content, str) else content
    assert isinstance(payload, dict)
    return payload


def _error_of(payload: dict[str, Any]) -> dict[str, Any]:
    assert set(payload) == {"error"}, payload
    error = payload["error"]
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert set(error) <= PAYLOAD_KEYS
    return dict(error)


def _run_graph(executor: Any, sql: str, scripted_model: Any, tool_call_message: Any) -> Any:
    """One run_sql call through the orchestrator graph; returns the invoke result."""
    from rootcause.agents.orchestrator import build_orchestrator

    model = scripted_model(
        tool_call_message("run_sql", {"sql": sql, "purpose": "p"}, "c1"),
        AIMessage(content="done"),
    )
    handle = build_orchestrator(
        model=model,
        settings=_settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        executor=executor,
    )
    return handle.graph.invoke(
        {"messages": [HumanMessage("go")]}, config={"configurable": {"thread_id": "t"}}
    )


def _run_sql_payload(result: Any) -> dict[str, Any]:
    message = next(
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "run_sql"
    )
    payload = json.loads(str(message.content))
    assert isinstance(payload, dict)
    return payload


# --- 1. approval predicate ---------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("make_error", INVALID_STATEMENT_ERRORS)
def test_invalid_statement_explain_error_does_not_interrupt_and_never_runs(
    make_error: Callable[[], Exception],
    make_executor: Callable[..., Any],
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
) -> None:
    executor = make_executor(error=make_error())

    result = _run_graph(executor, "select bad", scripted_model, tool_call_message)

    assert "__interrupt__" not in result
    assert executor.run_calls == []
    assert "error" in _run_sql_payload(result)


@pytest.mark.unit
@pytest.mark.parametrize("make_error", FAIL_CLOSED_ERRORS)
def test_other_explain_errors_still_fail_closed_with_an_interrupt(
    make_error: Callable[[], Exception],
    make_executor: Callable[..., Any],
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
) -> None:
    executor = make_executor(error=make_error())

    result = _run_graph(executor, "select 1", scripted_model, tool_call_message)

    assert "__interrupt__" in result
    assert executor.run_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(("rows", "interrupts"), [(THRESHOLD + 1, True), (THRESHOLD, False)])
def test_estimate_threshold_still_decides_when_explain_succeeds(
    rows: int,
    interrupts: bool,
    make_executor: Callable[..., Any],
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
) -> None:
    result = _run_graph(
        make_executor(estimated_rows=rows), "select 1", scripted_model, tool_call_message
    )

    assert ("__interrupt__" in result) is interrupts


# --- 2. run_sql never executes an invalid statement --------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("sql", [BINDER_UNKNOWN_COLUMN_SQL, VALIDATOR_UNKNOWN_COLUMN_SQL])
def test_run_sql_with_invalid_statement_returns_error_without_running(
    sql: str, real_executor: DuckDBExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_calls: list[str] = []

    def spy_run(sql: str, purpose: str) -> Any:
        run_calls.append(sql)
        raise AssertionError("run must not be reached for an invalid statement")

    monkeypatch.setattr(real_executor, "run", spy_run)

    payload = _invoke(_tools(real_executor)["run_sql"], sql)

    _error_of(payload)
    assert run_calls == []


# --- 3. actionable payload ---------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["run_sql", "explain_sql"])
def test_rejected_query_payload_lists_the_issue_codes(
    tool_name: str, real_executor: DuckDBExecutor
) -> None:
    payload = _invoke(_tools(real_executor)[tool_name], VALIDATOR_UNKNOWN_COLUMN_SQL)

    error = _error_of(payload)
    assert error["code"] == "QueryRejected"
    assert list(error["codes"]) == ["unknown_column"]


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["run_sql", "explain_sql"])
def test_rejected_query_payload_keeps_every_code_from_a_fake_executor(
    tool_name: str, make_executor: Callable[..., Any]
) -> None:
    executor = make_executor(error=QueryRejected(["unknown_column", "parse_error"]))

    error = _error_of(_invoke(_tools(executor)[tool_name], "select bad"))

    assert error["code"] == "QueryRejected"
    assert list(error["codes"]) == ["unknown_column", "parse_error"]
    # no validator reasons were supplied, so the detail is the static rewrite guidance only
    assert "describe_table" in error["detail"]


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["run_sql", "explain_sql"])
def test_binder_error_payload_carries_duckdb_detail_naming_the_column(
    tool_name: str, real_executor: DuckDBExecutor
) -> None:
    payload = _invoke(_tools(real_executor)[tool_name], BINDER_UNKNOWN_COLUMN_SQL)

    error = _error_of(payload)
    assert error["code"] == "QueryFailed"
    assert isinstance(error["detail"], str)
    assert "no_such_column" in error["detail"]
    assert len(error["detail"]) <= MAX_DETAIL


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["run_sql", "explain_sql"])
def test_binder_error_payload_carries_duckdb_candidate_suggestions(
    tool_name: str, real_executor: DuckDBExecutor
) -> None:
    payload = _invoke(_tools(real_executor)[tool_name], BINDER_CANDIDATES_SQL)

    detail = _error_of(payload)["detail"]
    assert "sum" in detail
    assert "candidate" in detail.lower()


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["run_sql", "explain_sql"])
def test_long_duckdb_detail_is_truncated(tool_name: str, real_executor: DuckDBExecutor) -> None:
    payload = _invoke(_tools(real_executor)[tool_name], BINDER_LONG_SQL)

    detail = _error_of(payload)["detail"]
    assert detail
    assert len(detail) <= MAX_DETAIL


@pytest.mark.unit
def test_cast_error_does_not_leak_the_row_value_into_payload_or_logs(
    real_executor: DuckDBExecutor,
) -> None:
    with capture_logs() as logs:
        payload = _invoke(_tools(real_executor)["run_sql"], CAST_SQL)

    error = _error_of(payload)
    assert error["code"] == "QueryFailed"
    assert "detail" not in error
    assert error["message"] == GENERIC_MESSAGE
    assert SENTINEL_VALUE not in json.dumps(payload)
    assert any(entry.get("event") == "sql_tool_failed" for entry in logs)  # logs were captured
    assert SENTINEL_VALUE not in repr(logs)


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["run_sql", "explain_sql"])
@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(lambda: QueryFailed("ConversionException"), id="ConversionException"),
        pytest.param(lambda: QueryFailed("ConstraintException"), id="ConstraintException"),
        pytest.param(lambda: QueryTimeout(1.0), id="QueryTimeout"),
        pytest.param(lambda: RuntimeError(f"boom {SENTINEL_VALUE}"), id="RuntimeError"),
    ],
)
def test_other_errors_carry_no_detail_and_a_generic_message(
    tool_name: str, make_error: Callable[[], Exception], make_executor: Callable[..., Any]
) -> None:
    executor = make_executor(error=make_error())

    payload = _invoke(_tools(executor)[tool_name], "select 1")

    error = _error_of(payload)
    assert "detail" not in error
    assert "codes" not in error
    assert error["message"] == GENERIC_MESSAGE
    assert SENTINEL_VALUE not in json.dumps(payload)


# --- 5. integration: orchestrator wiring over a real warehouse ---------------------


@pytest.mark.integration
def test_unknown_column_in_orchestrator_returns_detail_without_approval(
    real_executor: DuckDBExecutor,
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
) -> None:
    result = _run_graph(real_executor, BINDER_UNKNOWN_COLUMN_SQL, scripted_model, tool_call_message)

    assert "__interrupt__" not in result
    error = _error_of(_run_sql_payload(result))
    assert "no_such_column" in error["detail"]


@pytest.mark.integration
def test_validator_rejected_column_in_orchestrator_returns_codes_without_approval(
    real_executor: DuckDBExecutor,
    scripted_model: Callable[..., Any],
    tool_call_message: Callable[..., Any],
) -> None:
    result = _run_graph(
        real_executor, VALIDATOR_UNKNOWN_COLUMN_SQL, scripted_model, tool_call_message
    )

    assert "__interrupt__" not in result
    error = _error_of(_run_sql_payload(result))
    assert error["code"] == "QueryRejected"
    assert list(error["codes"]) == ["unknown_column"]
