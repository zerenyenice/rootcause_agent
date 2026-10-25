"""The root-cause SubAgent spec: name, skill prompt, tool allow-list, WhyChain output (RED)."""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import duckdb
import pytest
from langchain_core.tools import BaseTool, tool

from rootcause.agents.skills import SKILL_FILE, skill_paths


def _factory() -> Any:
    # Resolved at call time so the module can be absent while the tests are RED.
    return importlib.import_module("rootcause.agents.subagents").root_cause_subagent


ALLOWED = {"run_sql", "explain_sql", "list_tables", "describe_table", "column_values"}


@tool
def web_search(query: str) -> str:
    """A tool the root-cause subagent must never receive."""
    return query


@tool
def write_file_elsewhere(path: str) -> str:
    """Another tool outside the allow-list."""
    return path


@pytest.fixture
def tools(tmp_path: Path, executor: Any) -> list[BaseTool]:
    from rootcause.agents.tools.schema import make_schema_tools
    from rootcause.agents.tools.sql import make_sql_tools
    from rootcause.connections.duckdb_executor import DuckDBExecutor

    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR)")
    con.close()
    with DuckDBExecutor(path) as duck:
        return [
            *make_sql_tools(executor, 1_000),
            *make_schema_tools(duck, None),
            web_search,
            write_file_elsewhere,
        ]


def _skill_body() -> str:
    skill = next(p for p in skill_paths() if p.name == "root-cause")
    text = (skill / SKILL_FILE).read_text(encoding="utf-8")
    return re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL).strip()


@pytest.mark.unit
def test_name_and_description_mention_five_whys(tools: list[BaseTool]) -> None:
    root_cause_subagent = _factory()

    spec = root_cause_subagent(tools)

    assert spec["name"] == "root-cause"
    assert spec["description"].strip()
    assert "5 whys" in spec["description"].lower()


@pytest.mark.unit
def test_system_prompt_is_the_skill_body_with_the_executed_query_rule(
    tools: list[BaseTool],
) -> None:
    root_cause_subagent = _factory()

    prompt = root_cause_subagent(tools)["system_prompt"]

    assert _skill_body() in prompt
    assert "executed query" in prompt
    assert "name: root-cause" not in prompt  # the frontmatter is not part of the prompt


@pytest.mark.unit
def test_tools_are_filtered_to_the_sql_and_schema_allow_list(tools: list[BaseTool]) -> None:
    root_cause_subagent = _factory()

    names = [t.name for t in root_cause_subagent(tools)["tools"]]

    assert {t.name for t in tools} > ALLOWED  # the fixture really offers extras
    assert sorted(names) == sorted(ALLOWED)


@pytest.mark.unit
def test_sql_only_tools_yield_only_the_sql_subset(executor: Any) -> None:
    root_cause_subagent = _factory()
    from rootcause.agents.tools.sql import make_sql_tools

    spec = root_cause_subagent([*make_sql_tools(executor, 1_000), web_search])

    assert sorted(t.name for t in spec["tools"]) == ["explain_sql", "run_sql"]


@pytest.mark.unit
def test_no_tools_gives_an_empty_tool_list() -> None:
    root_cause_subagent = _factory()

    assert list(root_cause_subagent([web_search])["tools"]) == []


@pytest.mark.unit
def test_response_format_is_the_why_chain_model(tools: list[BaseTool]) -> None:
    from rootcause.agents.reports import WhyChain

    root_cause_subagent = _factory()

    assert root_cause_subagent(tools)["response_format"] is WhyChain


@pytest.mark.unit
def test_model_is_omitted_by_default_and_passed_through_when_given(
    tools: list[BaseTool], scripted_model: Any
) -> None:
    root_cause_subagent = _factory()

    model = scripted_model()

    assert "model" not in root_cause_subagent(tools)
    assert root_cause_subagent(tools, model)["model"] is model


@pytest.mark.unit
def test_input_tools_are_not_mutated(tools: list[BaseTool]) -> None:
    root_cause_subagent = _factory()

    before = list(tools)

    root_cause_subagent(tools)

    assert tools == before
