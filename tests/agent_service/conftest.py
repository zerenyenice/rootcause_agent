"""Fixtures for the agent service tests (helpers live in ``support.py``)."""

import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from tests.agent_service.support import GraphFactory, HandlerSpy, StubLangfuse, make_settings

ClientFactory = Callable[..., TestClient]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    from rootcause.settings import clear_settings_cache

    for name in [key for key in os.environ if key.upper().startswith("ROOTCAUSE_")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # no stray .env file is picked up
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture(autouse=True)
def reset_logging_state() -> Iterator[None]:
    yield
    import structlog

    structlog.reset_defaults()


@pytest.fixture
def make_client() -> Iterator[ClientFactory]:
    """Start the agent service around ``graph_factory`` and return a live TestClient."""
    with ExitStack() as stack:

        def _make(
            graph_factory: GraphFactory,
            settings: Any = None,
            *,
            connections: Any = None,
            catalog: Any = None,
            embedder: Any = None,
        ) -> TestClient:
            """Repositories are passed only when given; the defaults (SQL repositories on
            the unreachable test DATABASE_URL) must stay unused unless a run names a
            connection, so every existing test runs without Postgres."""
            from rootcause.agent_service.main import create_app

            seams = {
                name: value
                for name, value in (
                    ("connections", connections),
                    ("catalog", catalog),
                    ("embedder", embedder),
                )
                if value is not None
            }
            app = create_app(
                settings if settings is not None else make_settings(),
                graph_factory=graph_factory,
                checkpointer=InMemorySaver(),
                **seams,
            )
            return stack.enter_context(TestClient(app))

        yield _make


@pytest.fixture
def langfuse_spy(monkeypatch: pytest.MonkeyPatch) -> HandlerSpy:
    """Replace the handler factory ``run_config`` uses with a recording spy."""
    import rootcause.clients.langfuse as langfuse_module
    import rootcause.observability.tracing as tracing

    spy = HandlerSpy(handlers=[], calls=[])
    monkeypatch.setattr(tracing, "langfuse_callback_handler", spy)
    monkeypatch.setattr(langfuse_module, "Langfuse", StubLangfuse)
    langfuse_module.reset_langfuse_cache()
    return spy
