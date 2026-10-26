"""HTTP routes: liveness, threads, thread state and streaming runs."""

from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse

from rootcause.agent_service.connection_graphs import ConnectionGraphs
from rootcause.agent_service.runs import (
    Graph,
    build_run_config,
    run_input,
    stream_connection_run,
    stream_run,
)
from rootcause.agent_service.schemas import RunRequest, ThreadCreated, ThreadSearch
from rootcause.agent_service.sse import SSE_MEDIA_TYPE, to_json
from rootcause.agent_service.threads import ThreadRegistry, title_from_messages
from rootcause.settings import Settings

THREAD_NOT_FOUND = "Thread not found."
# Keep proxies from buffering or caching the event stream.
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

router = APIRouter()


@dataclass(frozen=True)
class AgentRuntime:
    """What the lifespan builds once: the default graph, the per-connection graphs and
    the thread registry."""

    graph: Graph
    threads: ThreadRegistry
    settings: Settings
    connection_graphs: ConnectionGraphs


def get_runtime(request: Request) -> AgentRuntime:
    runtime: AgentRuntime = request.app.state.agent
    return runtime


Runtime = Annotated[AgentRuntime, Depends(get_runtime)]


async def _known_thread(runtime: AgentRuntime, thread_id: UUID) -> str:
    canonical = str(thread_id)
    if not await runtime.threads.exists(canonical):
        raise HTTPException(status.HTTP_404_NOT_FOUND, THREAD_NOT_FOUND)
    return canonical


@router.get("/health")
@router.get("/health/live")
async def live() -> dict[str, str]:
    """Liveness: the process serves requests (no dependency checks)."""
    return {"status": "ok"}


@router.post("/threads", response_model=ThreadCreated)
async def create_thread(runtime: Runtime) -> ThreadCreated:
    """Create a thread; any request body is ignored."""
    return ThreadCreated(thread_id=runtime.threads.create())


@router.post("/threads/search")
async def search_threads(body: ThreadSearch, runtime: Runtime) -> Response:
    """Threads that have run, newest ``updated_at`` first (LangGraph ``threads.search`` shape)."""
    found = await runtime.threads.search(limit=body.limit, offset=body.offset)
    return Response(
        content=to_json([thread.as_json() for thread in found]), media_type="application/json"
    )


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread_id: UUID, runtime: Runtime) -> Response:
    """Delete a thread's metadata and checkpoints."""
    await runtime.threads.delete(str(thread_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/threads/{thread_id}/state")
async def thread_state(thread_id: UUID, runtime: Runtime) -> Response:
    """Current values, next nodes and pending interrupts of a thread."""
    canonical = await _known_thread(runtime, thread_id)
    snapshot = await runtime.graph.aget_state({"configurable": {"thread_id": canonical}})
    body: dict[str, Any] = {
        "values": snapshot.values,
        "next": list(snapshot.next),
        "interrupts": list(snapshot.interrupts),
    }
    return Response(content=to_json(body), media_type="application/json")


@router.post("/threads/{thread_id}/runs/stream")
async def stream_thread_run(
    thread_id: UUID, body: RunRequest, runtime: Runtime
) -> StreamingResponse:
    """Run the graph on the thread and stream LangGraph SSE events.

    With ``context.connection_id`` the run uses that connection's graph; a connection
    that cannot be used is reported in-band (see ``stream_connection_run``).
    """
    canonical = await _known_thread(runtime, thread_id)
    config = build_run_config(canonical, body, runtime.settings)
    connection_id = body.context.connection_id if body.context is not None else None
    title = title_from_messages(body.input.messages) if body.input is not None else None
    await runtime.threads.record_run(canonical, title=title, connection_id=connection_id)
    subgraphs = body.stream_subgraphs
    if connection_id is None:
        events = stream_run(
            runtime.graph, run_input(body), config, thread_id=canonical, subgraphs=subgraphs
        )
    else:
        resolve = partial(runtime.connection_graphs.graph_for, connection_id)
        events = stream_connection_run(
            resolve, run_input(body), config, thread_id=canonical, subgraphs=subgraphs
        )
    return StreamingResponse(events, media_type=SSE_MEDIA_TYPE, headers=STREAM_HEADERS)
