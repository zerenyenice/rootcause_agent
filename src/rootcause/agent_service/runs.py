"""Run execution: graph input, per-run config and the SSE event stream."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from rootcause.agent_service.schemas import RunRequest
from rootcause.agent_service.sse import sse_frame
from rootcause.errors import RootCauseError
from rootcause.logging import get_logger
from rootcause.observability.tracing import CONNECTION_ID_KEY, run_config
from rootcause.settings import Settings

STREAM_MODES: Final = ("values", "messages", "updates")
RUN_FAILED_MESSAGE: Final = "The agent run failed. Please try again."
UNAVAILABLE_CODE: Final = "connection_unavailable"
CONNECTION_UNAVAILABLE_MESSAGE: Final = "The data source could not be reached."
CONNECTION_REFUSAL_CODES: Final = frozenset(
    {"connection_not_found", "connection_not_verified", UNAVAILABLE_CODE}
)

_logger = get_logger(__name__)

Graph = CompiledStateGraph[Any, Any, Any, Any]


def run_input(request: RunRequest) -> Any:
    """Graph input: ``Command(resume=...)`` for a command, else the new messages."""
    if request.command is not None:
        return Command(resume=request.command.resume)
    if request.input is None:  # excluded by RunRequest's validator
        raise ValueError("a run needs input or a command")
    return {"messages": request.input.messages}


def build_run_config(thread_id: str, request: RunRequest, settings: Settings) -> RunnableConfig:
    """Server-built config (client ``config`` is never read): thread, tracing, connection."""
    connection_id = request.context.connection_id if request.context is not None else None
    config = run_config(thread_id, connection_id=connection_id, settings=settings)
    if connection_id is None:
        return config
    configurable = {**config.get("configurable", {}), CONNECTION_ID_KEY: connection_id}
    return RunnableConfig(**{**config, "configurable": configurable})


async def stream_run(
    graph: Graph, graph_input: Any, config: RunnableConfig, *, thread_id: str
) -> AsyncIterator[str]:
    """SSE frames of one run: ``metadata``, then the graph's events, or a final ``error``.

    A failure becomes one generic ``error`` event naming the exception type only;
    exception messages (which can carry SQL, row values or DSNs) never reach the
    client or the log.
    """
    run_id = str(uuid.uuid4())
    yield sse_frame("metadata", {"run_id": run_id, "thread_id": thread_id})
    async for frame in _graph_events(graph, graph_input, config, run_id, thread_id):
        yield frame


async def stream_connection_run(
    resolve_graph: Callable[[], Awaitable[Graph]],
    graph_input: Any,
    config: RunnableConfig,
    *,
    thread_id: str,
) -> AsyncIterator[str]:
    """Like :func:`stream_run`, on the graph ``resolve_graph`` returns for the run's
    connection.

    A connection that cannot be used ends the stream after ``metadata`` with one
    ``error`` event ``{"error": <code>, "message": <generic>}``: ``connection_not_found``,
    ``connection_not_verified`` or ``connection_unavailable`` (any other failure while
    preparing the graph). Nothing about the source (path, host, driver message) is sent.
    """
    run_id = str(uuid.uuid4())
    yield sse_frame("metadata", {"run_id": run_id, "thread_id": thread_id})
    try:
        graph = await resolve_graph()
    except Exception as error:  # noqa: BLE001 - the stream has started: report in-band
        code, message = _refusal(error)
        _logger.warning(
            "agent_run_refused",
            thread_id=thread_id,
            run_id=run_id,
            error=code,
            error_type=type(error).__name__,
        )
        yield sse_frame("error", {"error": code, "message": message})
        return
    async for frame in _graph_events(graph, graph_input, config, run_id, thread_id):
        yield frame


def _refusal(error: Exception) -> tuple[str, str]:
    if isinstance(error, RootCauseError) and error.code in CONNECTION_REFUSAL_CODES:
        return error.code, error.public_message
    return UNAVAILABLE_CODE, CONNECTION_UNAVAILABLE_MESSAGE


async def _graph_events(
    graph: Graph, graph_input: Any, config: RunnableConfig, run_id: str, thread_id: str
) -> AsyncIterator[str]:
    try:
        async for mode, chunk in graph.astream(graph_input, config, stream_mode=list(STREAM_MODES)):
            yield sse_frame(mode, chunk)
    except Exception as error:  # noqa: BLE001 - the stream has started: report in-band
        error_type = type(error).__name__
        _logger.error("agent_run_failed", thread_id=thread_id, run_id=run_id, error_type=error_type)
        yield sse_frame("error", {"error": error_type, "message": RUN_FAILED_MESSAGE})
