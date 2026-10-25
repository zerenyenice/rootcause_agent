"""POST /threads/{id}/runs/stream speaks LangGraph Server's SSE protocol (useStream)."""

import uuid
from typing import Any

import pytest

from tests.agent_service.conftest import ClientFactory
from tests.agent_service.support import (
    EVENT_NAMES,
    QUESTION,
    REPLY,
    chat_graph,
    connection_echo_graph,
    create_thread,
    human_input,
    last_messages,
    parse_sse,
    stream_run,
)

pytestmark = pytest.mark.unit


def test_stream_is_event_stream_with_strict_sse_frames(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    assert result.status_code == 200
    assert result.content_type.startswith("text/event-stream")
    events = parse_sse(result.raw)  # asserts the exact "event:/data:/blank" framing
    assert events
    assert {name for name, _ in events} <= EVENT_NAMES


def test_stream_starts_with_metadata_naming_run_and_thread(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    first_event, first_data = result.events[0]
    assert first_event == "metadata"
    assert first_data["thread_id"] == thread_id
    uuid.UUID(first_data["run_id"])
    assert result.names.count("metadata") == 1


def test_each_run_gets_its_own_run_id(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)
    first = stream_run(client, thread_id, human_input())
    client_two = make_client(chat_graph())
    other_thread = create_thread(client_two)
    second = stream_run(client_two, other_thread, human_input())

    assert first.named("metadata")[0]["run_id"] != second.named("metadata")[0]["run_id"]


def test_values_carry_the_full_state_with_the_ai_reply(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    messages = last_messages(result)
    human, ai = messages
    assert human["type"] == "human"
    assert human["content"] == QUESTION
    assert ai["type"] == "ai"
    assert ai["content"] == REPLY  # unicode, emoji and newline survive the framing
    assert isinstance(ai["id"], str) and ai["id"]
    assert isinstance(human["id"], str) and human["id"]
    assert ai["tool_calls"] == []
    assert "error" not in result.names


def test_messages_events_stream_the_reply_as_chunks(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    tuples = result.named("messages")
    assert len(tuples) > 1, "the reply must arrive token by token, not as one message"
    for item in tuples:
        assert isinstance(item, list) and len(item) == 2
        chunk, metadata = item
        assert chunk["type"] == "AIMessageChunk"
        assert isinstance(metadata, dict)
        assert metadata["langgraph_node"] == "agent"
    chunks = [chunk for chunk, _ in tuples]
    assert "".join(chunk["content"] for chunk in chunks) == REPLY
    assert len({chunk["id"] for chunk in chunks}) == 1
    assert chunks[0]["id"] == last_messages(result)[-1]["id"]


def test_updates_events_name_the_node_that_ran(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    updates = result.named("updates")
    agent_updates = [u["agent"] for u in updates if "agent" in u]
    assert agent_updates
    assert agent_updates[-1]["messages"][-1]["content"] == REPLY


def test_a_second_run_continues_the_same_thread(make_client: ClientFactory) -> None:
    client = make_client(connection_echo_graph())
    thread_id = create_thread(client)
    stream_run(client, thread_id, human_input("first"))

    result = stream_run(client, thread_id, human_input("second"))

    contents = [m["content"] for m in last_messages(result)]
    assert contents == ["first", "connection=None", "second", "connection=None"]


def test_stream_on_an_unknown_thread_is_404(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())

    result = stream_run(client, str(uuid.uuid4()), human_input())

    assert result.status_code == 404
    assert not result.content_type.startswith("text/event-stream")


@pytest.mark.parametrize(
    "body",
    [
        {"input": "not an object"},
        {"input": {"messages": "not a list"}},
        {"command": "resume"},
        {"context": ["not", "an", "object"]},
        ["not", "an", "object"],
    ],
    ids=["input-string", "messages-string", "command-string", "context-list", "body-list"],
)
def test_invalid_stream_body_is_422(make_client: ClientFactory, body: Any) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, body)

    assert result.status_code == 422


def test_non_json_stream_body_is_422(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)

    response = client.post(
        f"/threads/{thread_id}/runs/stream",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422


def test_extra_sdk_fields_are_accepted_but_client_config_is_ignored(
    make_client: ClientFactory,
) -> None:
    """useStream also posts ``config`` (with its own thread id) and ``streamSubgraphs``."""
    client = make_client(connection_echo_graph())
    thread_id = create_thread(client)
    other_thread = create_thread(client)
    body = {
        **human_input(),
        "context": None,
        "command": None,
        "streamSubgraphs": False,
        "config": {"configurable": {"thread_id": other_thread, "connection_id": "evil"}},
    }

    result = stream_run(client, thread_id, body)

    assert result.status_code == 200
    assert result.named("metadata")[0]["thread_id"] == thread_id
    assert last_messages(result)[-1]["content"] == "connection=None"
    other_state = client.get(f"/threads/{other_thread}/state").json()
    assert other_state["values"].get("messages", []) == []
