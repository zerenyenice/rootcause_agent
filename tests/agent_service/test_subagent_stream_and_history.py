"""Subgraph (subagent) events in the stream, and the thread history endpoints."""

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt

from tests.agent_service.conftest import ClientFactory
from tests.agent_service.support import (
    APPROVAL_REQUEST,
    chat_graph,
    create_thread,
    human_input,
    stream_run,
)

pytestmark = pytest.mark.unit


def _parent_with_subgraph(*, pause: bool) -> Any:
    """Parent graph whose ``tools`` node is a compiled subgraph (like DeepAgents' ``task``)."""

    def work(state: MessagesState) -> dict[str, Any]:
        if pause:
            interrupt(APPROVAL_REQUEST)
        return {"messages": [AIMessage(content="sub result")]}

    inner = StateGraph(MessagesState)
    inner.add_node("work", work)
    inner.add_edge(START, "work")
    inner.add_edge("work", END)
    outer = StateGraph(MessagesState)
    outer.add_node("tools", inner.compile())
    outer.add_edge(START, "tools")
    outer.add_edge("tools", END)

    def factory(*, checkpointer: Any, wiring: Any = None) -> Any:
        return outer.compile(checkpointer=checkpointer)

    return factory


def test_subgraph_events_are_named_with_their_namespace(make_client: ClientFactory) -> None:
    client = make_client(_parent_with_subgraph(pause=False))
    result = stream_run(client, create_thread(client), human_input())

    assert "error" not in result.names
    assert "updates" in result.names and "values" in result.names  # root keeps plain names
    nested = [n for n in result.names if "|" in n]
    assert nested, result.names
    mode, _, namespace = nested[0].partition("|")
    assert mode in {"values", "messages", "updates"}
    assert namespace.startswith("tools:")


def test_stream_subgraphs_false_keeps_only_root_events(make_client: ClientFactory) -> None:
    client = make_client(_parent_with_subgraph(pause=False))
    body = {**human_input(), "streamSubgraphs": False}

    result = stream_run(client, create_thread(client), body)

    assert not [n for n in result.names if "|" in n]


def test_interrupt_inside_a_subgraph_still_reaches_the_root_stream(
    make_client: ClientFactory,
) -> None:
    client = make_client(_parent_with_subgraph(pause=True))
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    root = [d for d in result.named("updates") if isinstance(d, dict) and "__interrupt__" in d]
    assert root and root[0]["__interrupt__"][0]["value"] == APPROVAL_REQUEST
    assert client.get(f"/threads/{thread_id}/state").json()["interrupts"]


def _search(client: Any, **body: Any) -> list[dict[str, Any]]:
    response = client.post("/threads/search", json=body)
    assert response.status_code == 200, response.text
    found: list[dict[str, Any]] = response.json()
    return found


def test_thread_search_lists_run_threads_newest_first_with_title(
    make_client: ClientFactory,
) -> None:
    client = make_client(chat_graph())
    first, second = create_thread(client), create_thread(client)
    create_thread(client)  # never run: not listed
    stream_run(client, first, human_input("  First\nquestion  "))
    stream_run(client, second, human_input("x" * 200))

    found = _search(client)

    assert [t["thread_id"] for t in found] == [second, first]
    assert found[1]["metadata"] == {"title": "First question", "connection_id": None}
    title = found[0]["metadata"]["title"]
    assert len(title) == 80 and title.endswith("…")
    assert set(found[0]) == {"thread_id", "created_at", "updated_at", "metadata"}


def test_a_new_run_bumps_updated_at_and_keeps_the_title(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    first, second = create_thread(client), create_thread(client)
    stream_run(client, first, human_input("one"))
    stream_run(client, second, human_input("two"))
    stream_run(client, first, human_input("a follow-up"))

    found = _search(client)

    assert [t["thread_id"] for t in found] == [first, second]
    assert found[0]["metadata"]["title"] == "one"


def test_thread_search_pages_and_validates(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    for _ in range(3):
        stream_run(client, create_thread(client), human_input())

    assert len(_search(client, limit=2)) == 2
    assert len(_search(client, limit=2, offset=2)) == 1
    assert client.post("/threads/search", json={"limit": 101}).status_code == 422


def test_delete_thread_removes_metadata_and_checkpoints(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)
    stream_run(client, thread_id, human_input())

    response = client.delete(f"/threads/{thread_id}")

    assert response.status_code == 204
    assert _search(client) == []
    assert client.get(f"/threads/{thread_id}/state").status_code == 404
