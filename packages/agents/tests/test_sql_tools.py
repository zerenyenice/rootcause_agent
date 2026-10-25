"""T030: run_sql / explain_sql tools over an injected QueryExecutor."""

import dataclasses
import json
from typing import Any

import pytest

THRESHOLD = 5_000_000


def tools_by_name(executor: Any, threshold: int = THRESHOLD) -> dict[str, object]:
    from rootcause_agents.tools.sql import make_sql_tools

    return {tool.name: tool for tool in make_sql_tools(executor, threshold)}


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
    out = tools_by_name(executor)["explain_sql"].invoke({"sql": "SELECT 1"})  # type: ignore[attr-defined]
    assert out["estimated_rows"] == 10
    assert out["estimated_cost"] == 1.5
    assert executor.explain_calls == ["SELECT 1"]
    assert executor.run_calls == []


@pytest.mark.unit
def test_run_sql_below_threshold_executes_and_is_json_serialisable(executor: Any) -> None:
    out = tools_by_name(executor)["run_sql"].invoke(  # type: ignore[attr-defined]
        {"sql": "SELECT 1", "purpose": "probe"}
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
    tools_by_name(executor)["run_sql"].invoke({"sql": "SELECT 1", "purpose": "p"})  # type: ignore[attr-defined]
    assert len(executor.run_calls) == 1


@pytest.mark.unit
def test_run_sql_above_threshold_requires_approval_and_does_not_run(make_executor: Any) -> None:
    executor = make_executor(estimated_rows=THRESHOLD + 1)
    out = tools_by_name(executor)["run_sql"].invoke(  # type: ignore[attr-defined]
        {"sql": "SELECT * FROM orders", "purpose": "scan"}
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
    out = tools_by_name(executor)[tool_name].invoke(args)  # type: ignore[attr-defined]
    assert out == {"error": {"code": "TimeoutError", "message": out["error"]["message"]}}
    assert "db slow" in out["error"]["message"]


@pytest.mark.unit
def test_sql_metacharacters_and_unicode_pass_through_verbatim(executor: Any) -> None:
    sql = "SELECT '; DROP TABLE x;--', '\U0001f600' -- ünï"
    out = tools_by_name(executor)["run_sql"].invoke({"sql": sql, "purpose": "p"})  # type: ignore[attr-defined]
    assert executor.run_calls == [(sql, "p")]
    assert out["sql"] == sql
