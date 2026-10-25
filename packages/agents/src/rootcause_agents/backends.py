"""Filesystem backend routing and checkpointer selection."""

from typing import Any

from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore

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


def build_checkpointer(settings: Settings) -> Any:
    """In-memory saver outside prod; otherwise an unopened Postgres saver factory.

    The Postgres value is a context manager the API lifespan enters; no
    connection is made here.
    """
    if settings.environment is not Environment.PROD:
        return InMemorySaver()
    from langgraph.checkpoint.postgres import PostgresSaver

    return PostgresSaver.from_conn_string(settings.database_url.get_secret_value())


__all__ = ["BaseCheckpointSaver", "build_backend", "build_checkpointer"]
