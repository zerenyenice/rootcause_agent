"""Agent service application factory (``uvicorn --factory ...:create_app``, port 8100).

The lifespan opens the Postgres checkpointer and store (unless a checkpointer is
injected), builds the graph once in a worker thread (graph construction may do
blocking file I/O) and on shutdown releases the clients and flushes telemetry. Middleware, error
envelopes and rate limiting are the API's own (:mod:`rootcause.api`).
"""

import asyncio
import functools
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.embeddings import Embeddings
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from limits import parse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import rootcause.api
from rootcause.agent_service.connection_graphs import ConnectionGraphs, GraphFactory
from rootcause.agent_service.persistence import open_persistence
from rootcause.agent_service.routes import AgentRuntime, router
from rootcause.agent_service.threads import ThreadRegistry
from rootcause.api.envelope import REQUEST_ID_HEADER
from rootcause.api.errors import register_exception_handlers
from rootcause.api.graphs import make_service_graph
from rootcause.api.main import configure_telemetry_once
from rootcause.api.middleware import (
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    create_limiter,
)
from rootcause.catalog.repository import CatalogRepository
from rootcause.clients import close_clients
from rootcause.connections.repository import ConnectionRepository
from rootcause.logging import configure_logging
from rootcause.settings import Environment, Settings, get_settings
from rootcause.telemetry import shutdown_telemetry

APP_TITLE: Final = "RootCause Agent Service"
CORS_METHODS: Final = ("GET", "POST")
CORS_HEADERS: Final = ("Content-Type", "Accept", REQUEST_ID_HEADER)


def _graph_factory(
    graph_factory: GraphFactory | None, store: BaseStore | None, settings: Settings
) -> GraphFactory:
    if graph_factory is not None:
        return graph_factory
    return functools.partial(make_service_graph, store=store, settings=settings)


@dataclass(frozen=True)
class _Seams:
    """Injected collaborators; ``None`` means the production default."""

    graph_factory: GraphFactory | None
    checkpointer: BaseCheckpointSaver[Any] | None
    connections: ConnectionRepository | None
    catalog: CatalogRepository | None
    embedder: Embeddings | None


def _lifespan(settings: Settings, seams: _Seams) -> Callable[[FastAPI], Any]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            # Shutdown (LIFO): close the connection executors, then the clients, then
            # flush buffered spans (blocking I/O).
            stack.push_async_callback(asyncio.to_thread, shutdown_telemetry)
            stack.push_async_callback(close_clients)
            saver, store = seams.checkpointer, None
            if saver is None:
                persistence = await stack.enter_async_context(open_persistence(settings))
                saver, store = persistence.checkpointer, persistence.store
            factory = _graph_factory(seams.graph_factory, store, settings)
            graph = await asyncio.to_thread(factory, checkpointer=saver)
            connection_graphs = ConnectionGraphs(
                factory=factory,
                checkpointer=saver,
                settings=settings,
                connections=seams.connections,
                catalog=seams.catalog,
                embedder=seams.embedder,
            )
            stack.push_async_callback(connection_graphs.close)
            app.state.agent = AgentRuntime(
                graph=graph,
                threads=ThreadRegistry(saver),
                settings=settings,
                connection_graphs=connection_graphs,
            )
            yield

    return lifespan


def create_app(
    settings: Settings | None = None,
    *,
    graph_factory: GraphFactory | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    connections: ConnectionRepository | None = None,
    catalog: CatalogRepository | None = None,
    embedder: Embeddings | None = None,
) -> FastAPI:
    """Build the agent service.

    ``graph_factory(checkpointer=...)`` builds the default graph once at startup and
    ``graph_factory(checkpointer=..., wiring=...)`` a connection's graph on its first
    run; it defaults to :func:`~rootcause.api.graphs.make_service_graph`.
    ``checkpointer`` defaults to the Postgres saver opened in the lifespan;
    ``connections``/``catalog`` to the SQL repositories on the application database
    and ``embedder`` to the configured embeddings client, all built on first use.
    """
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved)
    configure_telemetry_once(resolved)

    is_prod = resolved.environment is Environment.PROD
    app = FastAPI(
        title=APP_TITLE,
        version=rootcause.api.__version__,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
        lifespan=_lifespan(
            resolved, _Seams(graph_factory, checkpointer, connections, catalog, embedder)
        ),
    )
    app.state.settings = resolved
    app.state.limiter = create_limiter()
    app.state.rate_limit_item = parse(resolved.rate_limit)

    register_exception_handlers(app)
    app.include_router(router)

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=is_prod)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(  # outermost: preflights and error responses carry CORS headers
        CORSMiddleware,
        allow_origins=list(resolved.agent_allowed_origins),
        allow_methods=list(CORS_METHODS),
        allow_headers=list(CORS_HEADERS),
        allow_credentials=False,
    )
    FastAPIInstrumentor.instrument_app(app)
    return app
