"""T030: composite backend routing and checkpointer selection."""

from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause_core.settings import Settings

DB_URL = "postgresql://user:pw@localhost:5432/rootcause"


def settings_for(environment: str) -> Settings:
    return Settings(_env_file=None, database_url=DB_URL, environment=environment)


@pytest.mark.unit
def test_build_backend_routes_memories_and_skills_to_store() -> None:
    from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
    from rootcause_agents.backends import build_backend

    backend = build_backend(InMemoryStore())
    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.default, StateBackend)
    for prefix in ("/memories/", "/skills/"):
        assert isinstance(backend.routes[prefix], StoreBackend)
    assert set(backend.routes) == {"/memories/", "/skills/"}


@pytest.mark.unit
def test_memories_reach_store_and_scratch_stays_in_state(
    scripted_model: Callable[..., Any], tool_call_message: Callable[..., Any]
) -> None:
    from rootcause_agents.orchestrator import build_orchestrator

    model = scripted_model(
        tool_call_message(
            "write_file", {"file_path": "/memories/a.md", "content": "durable"}, "c1"
        ),
        tool_call_message(
            "write_file", {"file_path": "/scratch/a.md", "content": "ephemeral"}, "c2"
        ),
        AIMessage(content="done"),
    )
    store = InMemoryStore()
    handle = build_orchestrator(
        model=model, store=store, checkpointer=InMemorySaver(), settings=settings_for("test")
    )
    result = handle.graph.invoke(
        {"messages": [HumanMessage("remember")]},
        config={"configurable": {"thread_id": "m1"}},
    )
    stored = store.search(())
    assert len(stored) == 1
    assert "durable" in str(stored[0].value)
    assert "ephemeral" not in " ".join(str(item.value) for item in stored)
    assert "/scratch/a.md" in result["files"]


@pytest.mark.unit
@pytest.mark.parametrize("environment", ["dev", "test"])
def test_build_checkpointer_in_memory_outside_prod(environment: str) -> None:
    from rootcause_agents.backends import build_checkpointer

    assert isinstance(build_checkpointer(settings_for(environment)), InMemorySaver)


@pytest.mark.unit
def test_build_checkpointer_prod_is_postgres_factory_without_connecting() -> None:
    from rootcause_agents.backends import build_checkpointer

    result = build_checkpointer(settings_for("prod"))
    assert not isinstance(result, InMemorySaver)
    gen = getattr(result, "gen", None)
    labels = [
        type(result).__name__,
        type(result).__module__,
        getattr(result, "__qualname__", ""),
        getattr(gen, "__qualname__", ""),
    ]
    assert any("postgres" in label.lower() for label in labels), labels
