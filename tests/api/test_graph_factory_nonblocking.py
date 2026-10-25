"""make_graph() must not block the LangGraph Server event loop once warmed (RED).

LangGraph Server (langgraph-api) calls the registered graph factory for every run
inside its asyncio event loop with ``blockbuster`` active. Any synchronous file or
database I/O on that path raises ``BlockingError`` and fails the chat request, so
all such I/O has to happen once, outside the loop (import or first call).
"""

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from blockbuster import blockbuster_ctx

FAKE_ANTHROPIC_KEY = "sk-ant-SENTINEL-not-a-real-key"  # gitleaks:allow


def _in_blocking_checked_loop[T](call: Callable[[], T]) -> T:
    """Run ``call`` inside a running event loop with blockbuster active, like the server."""

    async def _run() -> T:
        with blockbuster_ctx():
            return call()

    return asyncio.run(_run())


@pytest.fixture
def warehouse_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Real settings (anthropic, fake key) and a real temp DuckDB warehouse; no network."""
    import rootcause.api.graphs as graphs
    from rootcause.clients.llm import reset_llm_cache
    from rootcause.settings import clear_settings_cache

    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR)")
    con.close()
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "test")
    monkeypatch.setenv("ROOTCAUSE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ROOTCAUSE_ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    monkeypatch.setenv("ROOTCAUSE_WAREHOUSE_PATH", str(path))
    # Isolate the process-wide executor cache so this test's warehouse is closed after it.
    executors: dict[Any, Any] = {}
    monkeypatch.setattr(graphs, "_executors", executors)
    clear_settings_cache()
    reset_llm_cache()
    yield
    for executor in executors.values():
        close = getattr(executor, "close", None)
        if close is not None:
            close()
    clear_settings_cache()
    reset_llm_cache()


@pytest.mark.unit
@pytest.mark.usefixtures("warehouse_env")
def test_warm_make_graph_called_inside_event_loop_does_no_blocking_io() -> None:
    from rootcause.api.graphs import make_graph

    make_graph()  # warm-up, synchronously and outside any event loop

    graph = _in_blocking_checked_loop(make_graph)

    assert graph is not None
    assert callable(getattr(graph, "ainvoke", None))
    assert graph.get_graph().nodes  # a compiled, non-empty graph


@pytest.mark.unit
@pytest.mark.usefixtures("warehouse_env")
def test_make_graph_inside_event_loop_is_repeatable_without_blocking_io() -> None:
    from rootcause.api.graphs import make_graph

    make_graph()

    first = _in_blocking_checked_loop(make_graph)
    second = _in_blocking_checked_loop(make_graph)

    assert first is not None
    assert second is not None


@pytest.mark.unit
def test_loaded_skill_body_is_served_without_file_io_inside_event_loop() -> None:
    from rootcause.agents.skills import load_skill_body
    from rootcause.agents.subagents import ROOT_CAUSE_SKILL

    warm = load_skill_body(ROOT_CAUSE_SKILL)

    again = _in_blocking_checked_loop(lambda: load_skill_body(ROOT_CAUSE_SKILL))

    assert again == warm
    assert again.strip()


@pytest.mark.unit
def test_unknown_skill_still_raises_file_not_found() -> None:
    from rootcause.agents.skills import load_skill_body

    with pytest.raises(FileNotFoundError, match="no-such-skill"):
        load_skill_body("no-such-skill")
