"""T030: run_sql / explain_sql tools over an injected QueryExecutor."""

import dataclasses
import json
from typing import Any

import pytest

THRESHOLD = 5_000_000


def tools_by_name(executor: Any, threshold: int = THRESHOLD) -> dict[str, object]:
    from rootcause_agents.tools.sql import make_sql_tools

    return {tool.name: tool for tool in make_sql_tools(executor, threshold)}


def invoke_tool(tool: Any, args: dict[str, Any]) -> Any:
    """Invoke with a full ToolCall (run_sql injects the call id) and decode the payload."""
    out = tool.invoke(
        {"name": tool.name, "args": args, "id": "call-1", "type": "tool_call"},
        config={"configurable": {"thread_id": "t"}},
    )
    content = getattr(out, "content", out)
    return json.loads(content) if isinstance(content, str) else content


@pytest.mark.unit
def test_dataclasses_are_frozen() -> None:
    from rootcause_agents.tools.sql import QueryEstimate, QueryResult

    estimate = QueryEstimate(estimated_rows=1, estimated_cost=2.0)
    result = QueryResult(columns=("a",), rows=((1,),), row_count=1, truncated=False, sql="s")
    with pytest.raises(dataclasses.FrozenInstanceError):
        estimate.estimated_rows = 2  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.row_count = 2  # type: ignore[misc]


@pytest.mark.unit
def test_make_sql_tools_names(executor: Any) -> None:
    assert set(tools_by_name(executor)) == {"explain_sql", "run_sql"}


@pytest.mark.unit
def test_explain_sql_returns_estimate_without_running(executor: Any) -> None:
    out = invoke_tool(tools_by_name(executor)["explain_sql"], {"sql": "SELECT 1"})
    assert out["estimated_rows"] == 10
    assert out["estimated_cost"] == 1.5
    assert executor.explain_calls == ["SELECT 1"]
    assert executor.run_calls == []


@pytest.mark.unit
def test_run_sql_below_threshold_executes_and_is_json_serialisable(executor: Any) -> None:
    out = invoke_tool(
        tools_by_name(executor)["run_sql"],
        {"sql": "SELECT 1", "purpose": "probe"},
    )
    assert executor.run_calls == [("SELECT 1", "probe")]
    assert out["columns"] == ["n"] or out["columns"] == ("n",)
    assert [list(r) for r in out["rows"]] == [[1]]
    assert out["row_count"] == 1
    assert out["truncated"] is False
    assert out["sql"] == "SELECT 1"
    json.dumps(out)


@pytest.mark.unit
def test_run_sql_at_threshold_still_executes(make_executor: Any) -> None:
    executor = make_executor(estimated_rows=THRESHOLD)
    invoke_tool(tools_by_name(executor)["run_sql"], {"sql": "SELECT 1", "purpose": "p"})
    assert len(executor.run_calls) == 1


@pytest.mark.unit
def test_run_sql_above_threshold_requires_approval_and_does_not_run(make_executor: Any) -> None:
    executor = make_executor(estimated_rows=THRESHOLD + 1)
    out = invoke_tool(
        tools_by_name(executor)["run_sql"],
        {"sql": "SELECT * FROM orders", "purpose": "scan"},
    )
    assert out == {
        "approval_required": True,
        "estimated_rows": THRESHOLD + 1,
        "threshold": THRESHOLD,
        "sql": "SELECT * FROM orders",
    }
    assert executor.run_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["run_sql", "explain_sql"])
def test_executor_exception_becomes_error_payload(tool_name: str, make_executor: Any) -> None:
    executor = make_executor(error=TimeoutError("db slow"))
    args = {"sql": "SELECT 1", "purpose": "p"} if tool_name == "run_sql" else {"sql": "SELECT 1"}
    out = invoke_tool(tools_by_name(executor)[tool_name], args)
    assert set(out) == {"error"}
    assert out["error"]["code"] == "TimeoutError"
    assert isinstance(out["error"]["message"], str)
    assert out["error"]["message"]


@pytest.mark.unit
def test_sql_metacharacters_and_unicode_pass_through_verbatim(executor: Any) -> None:
    sql = "SELECT '; DROP TABLE x;--', '\U0001f600' -- ünï"
    out = invoke_tool(tools_by_name(executor)["run_sql"], {"sql": sql, "purpose": "p"})
    assert executor.run_calls == [(sql, "p")]
    assert out["sql"] == sql


class _LeakyError(Exception):
    pass


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["run_sql", "explain_sql"])
def test_error_payload_does_not_leak_connection_secrets(tool_name: str, make_executor: Any) -> None:
    executor = make_executor(
        error=_LeakyError("could not connect to postgresql://u:secret@h/db: refused")
    )
    args = {"sql": "SELECT 1", "purpose": "p"} if tool_name == "run_sql" else {"sql": "SELECT 1"}
    out = invoke_tool(tools_by_name(executor)[tool_name], args)
    assert out["error"]["code"] == "_LeakyError"
    assert "secret" not in out["error"]["message"]
    assert "postgresql://" not in out["error"]["message"]
    assert "secret" not in json.dumps(out)


@pytest.mark.unit
def test_run_sql_normalises_non_json_native_values(make_executor: Any) -> None:
    import datetime as dt
    import decimal
    import uuid

    from rootcause_agents.tools.sql import QueryResult

    row = (
        decimal.Decimal("1.5"),
        dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC),
        dt.date(2026, 1, 2),
        uuid.UUID("12345678-1234-5678-1234-567812345678"),
    )

    class Rich(type(make_executor())):  # type: ignore[misc]
        def run(self, sql: str, purpose: str) -> Any:
            return QueryResult(
                columns=("d", "ts", "day", "id"), rows=(row,), row_count=1, truncated=False, sql=sql
            )

    out = invoke_tool(tools_by_name(Rich())["run_sql"], {"sql": "SELECT 1", "purpose": "p"})
    encoded = json.loads(json.dumps(out))
    assert len(encoded["rows"]) == 1
    assert len(encoded["rows"][0]) == 4
    assert "1.5" in str(encoded["rows"][0][0])
    assert "2026-01-02" in str(encoded["rows"][0][1])
    assert "2026-01-02" in str(encoded["rows"][0][2])
    assert "12345678-1234-5678-1234-567812345678" in str(encoded["rows"][0][3])


@pytest.mark.unit
def test_cheap_run_sql_calls_explain_at_most_once(executor: Any) -> None:
    invoke_tool(tools_by_name(executor)["run_sql"], {"sql": "SELECT 1", "purpose": "p"})
    assert len(executor.explain_calls) <= 1
    assert len(executor.run_calls) == 1
