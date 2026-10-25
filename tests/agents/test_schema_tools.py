"""Schema-introspection tools over a warehouse executor (RED)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from langchain_core.tools import BaseTool
from rootcause.agents.tools.schema import make_schema_tools  # type: ignore[import-not-found]
from rootcause.connections.duckdb_executor import (  # type: ignore[import-not-found]
    DuckDBExecutor,
)

from rootcause.evals.manufacturing.table_docs import TableDoc

DOCS = {
    "plants": TableDoc("Manufacturing sites.", {"plant_id": "Plant identifier."}),
}
LEAK = "SENTINEL_INTERNAL_PATH_/var/secret/warehouse.duckdb"


@pytest.fixture
def executor(tmp_path: Path) -> Iterator[DuckDBExecutor]:
    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR, plant_name VARCHAR)")
    con.execute("INSERT INTO plants VALUES ('P01','Alpha'),('P02','Beta')")
    con.execute("CREATE TABLE production (ts INTEGER, units INTEGER)")
    con.execute("INSERT INTO production SELECT i, i FROM range(200) t(i)")
    con.close()
    with DuckDBExecutor(path) as ex:
        yield ex


def _by_name(tools: list[BaseTool]) -> dict[str, BaseTool]:
    return {t.name: t for t in tools}


@pytest.mark.unit
def test_exposes_the_three_named_tools(executor: DuckDBExecutor) -> None:
    tools = make_schema_tools(executor, DOCS)
    assert sorted(t.name for t in tools) == ["column_values", "describe_table", "list_tables"]
    assert all(t.description for t in tools)


@pytest.mark.unit
def test_list_tables_has_name_row_count_and_description(executor: DuckDBExecutor) -> None:
    out: dict[str, Any] = _by_name(make_schema_tools(executor, DOCS))["list_tables"].invoke({})
    rows = {t["name"]: t for t in out["tables"]}
    assert rows["plants"] == {
        "name": "plants",
        "row_count": 2,
        "description": "Manufacturing sites.",
    }
    assert rows["production"]["row_count"] == 200
    assert rows["production"]["description"] == ""


@pytest.mark.unit
def test_list_tables_without_docs_gives_empty_descriptions(executor: DuckDBExecutor) -> None:
    out = _by_name(make_schema_tools(executor))["list_tables"].invoke({})
    assert {t["description"] for t in out["tables"]} == {""}


@pytest.mark.unit
def test_describe_table_has_name_type_and_description(executor: DuckDBExecutor) -> None:
    tool = _by_name(make_schema_tools(executor, DOCS))["describe_table"]
    out = tool.invoke({"table": "plants"})
    columns = {c["name"]: c for c in out["columns"]}
    assert columns["plant_id"]["type"].upper() == "VARCHAR"
    assert columns["plant_id"]["description"] == "Plant identifier."
    assert columns["plant_name"]["description"] == ""
    assert list(columns) == ["plant_id", "plant_name"]


@pytest.mark.unit
def test_column_values_returns_distinct_values_capped_at_fifty(
    executor: DuckDBExecutor,
) -> None:
    tools = _by_name(make_schema_tools(executor, DOCS))
    small = tools["column_values"].invoke({"table": "plants", "column": "plant_id"})
    assert sorted(small["values"]) == ["P01", "P02"]
    big = tools["column_values"].invoke({"table": "production", "column": "ts"})
    assert len(big["values"]) == 50


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("describe_table", {"table": "nope"}),
        ("describe_table", {"table": "plants; DROP TABLE plants"}),
        ("column_values", {"table": "nope", "column": "x"}),
        ("column_values", {"table": "plants", "column": "nope"}),
    ],
)
def test_unknown_names_return_error_payload_without_raising(
    executor: DuckDBExecutor, tool_name: str, args: dict[str, str]
) -> None:
    out = _by_name(make_schema_tools(executor, DOCS))[tool_name].invoke(args)
    assert "error" in out
    assert "values" not in out
    assert "columns" not in out


class _ExplodingExecutor:
    def list_tables(self) -> Any:
        raise RuntimeError(LEAK)

    def describe_table(self, name: str) -> Any:
        raise RuntimeError(LEAK)

    def distinct_values(self, table: str, column: str, limit: int = 50) -> Any:
        raise RuntimeError(LEAK)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("list_tables", {}),
        ("describe_table", {"table": "plants"}),
        ("column_values", {"table": "plants", "column": "plant_id"}),
    ],
)
def test_internal_failures_do_not_leak_details(tool_name: str, args: dict[str, str]) -> None:
    tools = _by_name(make_schema_tools(_ExplodingExecutor(), DOCS))
    out = tools[tool_name].invoke(args)
    assert "error" in out
    assert LEAK not in str(out)
