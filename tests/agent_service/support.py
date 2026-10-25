"""Helpers for the agent service tests: scripted graphs, settings, SSE parsing.

The service under test is ``rootcause.agent_service.main.create_app``. Every graph
here is a tiny LangGraph that the service compiles through
``graph_factory(checkpointer=...)`` with an in-memory checkpointer, so no test needs
a network, an LLM or Postgres.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt

DATABASE_URL = "postgresql+asyncpg://u:p@127.0.0.1:1/db"
LEAKED_SECRET = "SENTINEL-secret-dsn-postgres://admin:SENTINEL@db"  # gitleaks:allow
QUESTION = "Why did line 3 stop?"
# Non-ASCII, emoji and a newline: the reply must survive JSON-in-SSE framing intact.
REPLY = "Kök neden:\nhat 3 🔧 durdu"
APPROVAL_REQUEST = {
    "action_requests": [{"name": "run_sql", "args": {"query": "SELECT 1"}}],
    "review_configs": [{"action_name": "run_sql", "allowed_decisions": ["approve", "reject"]}],
}
EVENT_NAMES = frozenset({"metadata", "values", "messages", "updates", "error"})

GraphFactory = Callable[..., Any]


# --- SSE -----------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamResult:
    status_code: int
    content_type: str
    raw: str
    events: tuple[tuple[str, Any], ...]

    def named(self, name: str) -> list[Any]:
        return [data for event, data in self.events if event == name]

    @property
    def names(self) -> list[str]:
        return [event for event, _ in self.events]


def parse_sse(raw: str) -> tuple[tuple[str, Any], ...]:
    """Strictly parse ``event: <name>\\ndata: <json>\\n\\n`` frames (asserts on anything else)."""
    assert raw.endswith("\n\n"), f"stream does not end with a blank line: {raw[-40:]!r}"
    frames = raw[: -len("\n\n")].split("\n\n")
    events: list[tuple[str, Any]] = []
    for frame in frames:
        lines = frame.split("\n")
        assert len(lines) == 2, f"frame is not exactly 'event:' + 'data:' lines: {frame!r}"
        event_line, data_line = lines
        assert event_line.startswith("event: "), frame
        assert data_line.startswith("data: "), frame
        events.append((event_line[len("event: ") :], json.loads(data_line[len("data: ") :])))
    return tuple(events)


def stream_run(client: TestClient, thread_id: str, body: Any) -> StreamResult:
    path = f"/threads/{thread_id}/runs/stream"
    with client.stream("POST", path, json=body) as response:
        raw = "".join(response.iter_text())
        status = response.status_code
        content_type = response.headers.get("content-type", "")
    events = parse_sse(raw) if status == 200 else ()
    return StreamResult(status, content_type, raw, events)


def human_input(text: str = QUESTION) -> dict[str, Any]:
    return {"input": {"messages": [{"type": "human", "content": text}]}}


def create_thread(client: TestClient) -> str:
    response = client.post("/threads", json={})
    assert response.status_code in (200, 201), response.text
    return str(response.json()["thread_id"])


def last_messages(result: StreamResult) -> list[dict[str, Any]]:
    values = [v for v in result.named("values") if "messages" in v]
    assert values, f"no values event with messages: {result.names}"
    messages: list[dict[str, Any]] = values[-1]["messages"]
    return messages


# --- scripted graphs -----------------------------------------------------------------


class _FailingModel(BaseChatModel):
    """A chat model whose every call raises an error carrying a secret-looking message."""

    @property
    def _llm_type(self) -> str:
        return "failing-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError(LEAKED_SECRET)


def _single_node(name: str, node: Callable[..., Any]) -> GraphFactory:
    builder = StateGraph(MessagesState)
    builder.add_node(name, node)
    builder.add_edge(START, name)
    builder.add_edge(name, END)

    def factory(*, checkpointer: Any) -> Any:
        return builder.compile(checkpointer=checkpointer)

    return factory


def _model_graph(model: BaseChatModel) -> GraphFactory:
    async def agent(state: MessagesState, config: RunnableConfig) -> dict[str, Any]:
        return {"messages": [await model.ainvoke(state["messages"], config)]}

    return _single_node("agent", agent)


def chat_graph(reply: str = REPLY) -> GraphFactory:
    """One ``agent`` node calling a scripted, token-streaming fake model."""
    return _model_graph(GenericFakeChatModel(messages=iter([AIMessage(content=reply)])))


def failing_graph() -> GraphFactory:
    return _model_graph(_FailingModel())


def approval_graph() -> GraphFactory:
    """One ``approve`` node that pauses on ``interrupt`` and reports the resume value."""

    def approve(state: MessagesState) -> dict[str, Any]:
        decision = interrupt(APPROVAL_REQUEST)
        kind = decision["decisions"][0]["type"]
        return {"messages": [AIMessage(content=f"decision: {kind}")]}

    return _single_node("approve", approve)


def connection_echo_graph() -> GraphFactory:
    """One ``echo`` node replying with the connection id the run config carries."""

    def echo(state: MessagesState, config: RunnableConfig) -> dict[str, Any]:
        connection_id = config.get("configurable", {}).get("connection_id")
        return {"messages": [AIMessage(content=f"connection={connection_id}")]}

    return _single_node("echo", echo)


# --- app -----------------------------------------------------------------------------


def make_settings(**overrides: Any) -> Any:
    from rootcause.settings import Settings

    return Settings(database_url=DATABASE_URL, **overrides)


# --- Langfuse spy --------------------------------------------------------------------


class RecordingHandler(BaseCallbackHandler):
    """Stands in for the Langfuse handler: records root-run metadata."""

    def __init__(self) -> None:
        self.root_metadata: list[dict[str, Any]] = []

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if parent_run_id is None:
            self.root_metadata.append(dict(metadata or {}))


@dataclass
class HandlerSpy:
    handlers: list[RecordingHandler]
    calls: list[Any]

    def __call__(self, settings: Any = None) -> RecordingHandler:
        self.calls.append(settings)
        handler = RecordingHandler()
        self.handlers.append(handler)
        return handler


class StubLangfuse:
    """No-network replacement for the SDK client (in case the service builds one)."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def flush(self) -> None:
        return None

    def shutdown(self) -> None:
        return None
