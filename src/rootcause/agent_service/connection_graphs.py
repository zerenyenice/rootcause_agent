"""Per-connection orchestrator graphs of the agent service (Sensitive).

A run naming ``context.connection_id`` uses a graph wired to that connection's
selected catalog (:mod:`rootcause.catalog.agent_tools`). On EVERY such run the
stored connection is re-read: it must still exist and be verified read-only, and
its current selection is loaded. Graphs are cached per connection and keyed by a
fingerprint of the login and the selection (ids, names, descriptions), so catalog
edits take effect on the next run without a restart; unchanged runs reuse the
cached graph. The open executor is kept per connection and reused across selection
changes; it is replaced (and the old one closed) only when the login changes.
Every executor is closed at shutdown.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.embeddings import Embeddings
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncEngine

from rootcause.agent_service.runs import Graph
from rootcause.catalog.agent_tools import load_selection, open_record_executor, wire
from rootcause.catalog.repository import CatalogRepository, SqlCatalogRepository
from rootcause.catalog.restricted_executor import SourceExecutor
from rootcause.clients.embeddings import LazyEmbeddings
from rootcause.connections.repository import (
    ConnectionNotFound,
    ConnectionRepository,
    SqlConnectionRepository,
)
from rootcause.db.engine import make_async_engine
from rootcause.logging import get_logger
from rootcause.settings import Settings

GraphFactory = Callable[..., Graph]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Entry:
    login: str
    fingerprint: str
    executor: SourceExecutor
    graph: Graph


def parse_connection_id(raw: str) -> uuid.UUID:
    """The id as a UUID; anything else names no connection (:class:`ConnectionNotFound`)."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        pass
    raise ConnectionNotFound()


async def _close(executor: SourceExecutor) -> None:
    try:
        await asyncio.to_thread(executor.close)
    except Exception as error:  # noqa: BLE001 - closing the rest matters more
        _logger.warning("connection_executor_close_failed", error_type=type(error).__name__)


class ConnectionGraphs:
    """Builds, caches and closes the graphs of individual connections."""

    def __init__(
        self,
        *,
        factory: GraphFactory,
        checkpointer: BaseCheckpointSaver[Any],
        settings: Settings,
        connections: ConnectionRepository | None = None,
        catalog: CatalogRepository | None = None,
        embedder: Embeddings | None = None,
    ) -> None:
        self._factory = factory
        self._checkpointer = checkpointer
        self._settings = settings
        self._connections = connections
        self._catalog = catalog
        self._embedder = embedder if embedder is not None else LazyEmbeddings(settings)
        self._engine: AsyncEngine | None = None
        self._entries: dict[uuid.UUID, _Entry] = {}
        self._lock = asyncio.Lock()

    def _repositories(self) -> tuple[ConnectionRepository, CatalogRepository]:
        """The injected repositories, or SQL ones on the application database (lazy)."""
        if self._connections is None or self._catalog is None:
            if self._engine is None:
                self._engine = make_async_engine(self._settings)
            if self._connections is None:
                self._connections = SqlConnectionRepository(self._engine)
            if self._catalog is None:
                self._catalog = SqlCatalogRepository(self._engine)
        return self._connections, self._catalog

    async def graph_for(self, raw_connection_id: str) -> Graph:
        """The graph of a verified connection (built in a worker thread on first use).

        Raises:
            ConnectionNotFound: unknown id, or not a UUID.
            ConnectionNotVerified: the connection is not verified read-only.
            ConnectionUnavailable: the source cannot be opened.
        """
        connection_id = parse_connection_id(raw_connection_id)
        connections, catalog = self._repositories()
        selection = await load_selection(connection_id, connections=connections, catalog=catalog)
        cached = self._entries.get(connection_id)
        if cached is not None and cached.fingerprint == selection.fingerprint:
            return cached.graph
        async with self._lock:
            current = self._entries.get(connection_id)
            if current is not None and current.fingerprint == selection.fingerprint:
                return current.graph
            reuse = current is not None and current.login == selection.login_fingerprint
            executor = (
                current.executor
                if current is not None and reuse
                else await open_record_executor(selection.record, self._settings)
            )
            try:
                wiring = wire(
                    selection,
                    executor,
                    catalog=catalog,
                    embedder=self._embedder,
                    settings=self._settings,
                )
                graph = await asyncio.to_thread(
                    self._factory, checkpointer=self._checkpointer, wiring=wiring
                )
            except BaseException:
                if not reuse:
                    await _close(executor)
                raise
            entry = _Entry(selection.login_fingerprint, selection.fingerprint, executor, graph)
            self._entries = {**self._entries, connection_id: entry}
            _logger.info(
                "connection_graph_built", connection_id=str(connection_id), reused_executor=reuse
            )
        if current is not None and not reuse:
            await _close(current.executor)
        return graph

    async def close(self) -> None:
        """Close every connection's executor and the lazily built engine (shutdown)."""
        async with self._lock:
            entries, self._entries = list(self._entries.values()), {}
        for entry in entries:
            await _close(entry.executor)
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
