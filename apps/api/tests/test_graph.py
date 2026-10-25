"""langgraph.json registers a placeholder graph that compiles and echoes."""

import importlib
import json
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
LANGGRAPH_JSON = API_ROOT / "langgraph.json"


def _placeholder_spec() -> str:
    return str(json.loads(LANGGRAPH_JSON.read_text())["graphs"]["placeholder"])


@pytest.mark.unit
def test_langgraph_json_exists_and_parses() -> None:
    config = json.loads(LANGGRAPH_JSON.read_text())

    assert isinstance(config["graphs"], dict)
    assert "placeholder" in config["graphs"]


@pytest.mark.unit
def test_placeholder_points_at_existing_file_and_attribute() -> None:
    file_part, _, attribute = _placeholder_spec().partition(":")

    target = (API_ROOT / file_part).resolve()

    assert target.is_file()
    assert attribute == "graph"
    assert f"{attribute} =" in target.read_text() or f"{attribute}:" in target.read_text()


@pytest.mark.unit
def test_graph_module_exposes_compiled_graph() -> None:
    module = importlib.import_module("rootcause_api.graphs")

    assert hasattr(module.graph, "invoke")
    assert type(module.graph).__name__ == "CompiledStateGraph"


@pytest.mark.unit
def test_graph_echoes_input_message() -> None:
    from rootcause_api.graphs import graph

    result = graph.invoke({"messages": ["hi"]})

    assert isinstance(result, dict)
    assert result["messages"][0] == "hi"
    assert result["messages"][-1] == "echo: hi"


@pytest.mark.unit
def test_graph_handles_empty_messages() -> None:
    from rootcause_api.graphs import graph

    result = graph.invoke({"messages": []})

    assert isinstance(result["messages"], list)
