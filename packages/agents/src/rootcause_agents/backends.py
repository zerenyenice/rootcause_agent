"""Filesystem backend routing and checkpointer selection."""

from contextlib import AbstractAsyncContextManager
from typing import Any

from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import BaseStore

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Environment, Settings

PERSISTENT_ROUTES = ("/memories/", "/skills/")
_STORE_NAMESPACE = ("filesystem",)


def build_backend(store: BaseStore | None) -> CompositeBackend:
    """Persist ``/memories/`` and ``/skills/`` in the store; everything else per thread."""
    routes: dict[str, BackendProtocol] = {
        prefix: StoreBackend(namespace=lambda _rt: _STORE_NAMESPACE, store=store)
        for prefix in PERSISTENT_ROUTES
    }
    return CompositeBackend(default=StateBackend(), routes=routes)


def build_checkpointer(settings: Settings) -> BaseCheckpointSaver[Any]:
    """In-memory saver outside prod.

    Prod uses :func:`postgres_checkpointer_cm` instead; this raises there.
    """
    if settings.environment is Environment.PROD:
        raise ConfigurationError(
            "Use postgres_checkpointer_cm in prod and pass the opened saver explicitly."
        )
    return InMemorySaver()


def postgres_checkpointer_cm(
    settings: Settings,
) -> AbstractAsyncContextManager[AsyncPostgresSaver]:
    """Unopened async context manager yielding an ``AsyncPostgresSaver``.

    No connection is made here. The T035 API lifespan does
    ``async with postgres_checkpointer_cm(settings) as saver: await saver.setup()``.
    """
    return AsyncPostgresSaver.from_conn_string(settings.database_url.get_secret_value())


__all__ = [
    "BaseCheckpointSaver",
    "build_backend",
    "build_checkpointer",
    "postgres_checkpointer_cm",
]
