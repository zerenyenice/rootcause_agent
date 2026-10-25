"""T008: build_orchestrator obtains its default model from rootcause_core.clients."""

from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit


def _settings() -> Settings:
    return Settings(
        _env_file=None, database_url="postgresql://u:p@localhost/db", environment="test"
    )


def test_default_model_comes_from_clients_chat_model(
    monkeypatch: pytest.MonkeyPatch, scripted_model: Callable[..., Any]
) -> None:
    import rootcause_core.clients as clients

    import rootcause_agents.orchestrator as orchestrator_module

    fake = scripted_model(AIMessage(content="hi"))
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def recording_chat_model(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return fake

    monkeypatch.setattr(clients, "chat_model", recording_chat_model)
    monkeypatch.setattr(orchestrator_module, "chat_model", recording_chat_model, raising=False)
    settings = _settings()

    handle = orchestrator_module.build_orchestrator(
        settings=settings, store=InMemoryStore(), checkpointer=InMemorySaver()
    )

    assert handle.graph is not None
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert (args[0] if args else kwargs["role"]) == "orchestrator"
    passed_settings = args[1] if len(args) > 1 else kwargs.get("settings")
    assert passed_settings in (None, settings)


def test_injected_model_bypasses_clients(
    monkeypatch: pytest.MonkeyPatch, scripted_model: Callable[..., Any]
) -> None:
    import rootcause_core.clients as clients

    import rootcause_agents.orchestrator as orchestrator_module

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("chat_model must not be called when a model is injected")

    monkeypatch.setattr(clients, "chat_model", fail)
    monkeypatch.setattr(orchestrator_module, "chat_model", fail, raising=False)

    handle = orchestrator_module.build_orchestrator(
        model=scripted_model(AIMessage(content="hi")),
        settings=_settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
    )

    assert handle.graph is not None
