"""HTTP routes: liveness, threads, thread state and streaming runs."""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse

from rootcause.agent_service.runs import Graph, build_run_config, run_input, stream_run
from rootcause.agent_service.schemas import RunRequest, ThreadCreated
from rootcause.agent_service.sse import SSE_MEDIA_TYPE, to_json
from rootcause.agent_service.threads import ThreadRegistry
from rootcause.settings import Settings

THREAD_NOT_FOUND = "Thread not found."
# Keep proxies from buffering or caching the event stream.
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

router = APIRouter()


@dataclass(frozen=True)
class AgentRuntime:
    """What the lifespan builds once: the compiled graph and the thread registry."""

    graph: Graph
    threads: ThreadRegistry
    settings: Settings


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
    """Run the graph on the thread and stream LangGraph SSE events."""
    canonical = await _known_thread(runtime, thread_id)
    config = build_run_config(canonical, body, runtime.settings)
    events = stream_run(runtime.graph, run_input(body), config, thread_id=canonical)
    return StreamingResponse(events, media_type=SSE_MEDIA_TYPE, headers=STREAM_HEADERS)
