"""Threads, state and liveness endpoints of the agent service."""

import uuid

import pytest

from tests.agent_service.conftest import ClientFactory
from tests.agent_service.support import (
    QUESTION,
    REPLY,
    chat_graph,
    create_thread,
    human_input,
    stream_run,
)

pytestmark = pytest.mark.unit


def test_health_live_returns_200(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())

    response = client.get("/health/live")

    assert response.status_code == 200


def test_create_thread_returns_a_fresh_uuid_thread_id(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())

    first = client.post("/threads", json={})
    second = client.post("/threads", json={})

    assert first.status_code in (200, 201)
    assert second.status_code in (200, 201)
    first_id = first.json()["thread_id"]
    second_id = second.json()["thread_id"]
    assert str(uuid.UUID(first_id)) == first_id
    assert first_id != second_id


def test_create_thread_accepts_an_empty_request_body(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())

    response = client.post("/threads")

    assert response.status_code in (200, 201)
    uuid.UUID(response.json()["thread_id"])


def test_state_of_a_new_thread_has_no_pending_work(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)

    response = client.get(f"/threads/{thread_id}/state")

    assert response.status_code == 200
    body = response.json()
    assert body["next"] == []
    assert body["interrupts"] == []


def test_state_after_a_run_holds_the_conversation(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    thread_id = create_thread(client)
    assert stream_run(client, thread_id, human_input()).status_code == 200

    response = client.get(f"/threads/{thread_id}/state")

    assert response.status_code == 200
    body = response.json()
    messages = body["values"]["messages"]
    assert [m["type"] for m in messages] == ["human", "ai"]
    assert messages[0]["content"] == QUESTION
    assert messages[1]["content"] == REPLY
    assert body["next"] == []
    assert body["interrupts"] == []


def test_state_of_an_unknown_thread_is_404(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())

    response = client.get(f"/threads/{uuid.uuid4()}/state")

    assert response.status_code == 404


def test_threads_are_isolated_from_each_other(make_client: ClientFactory) -> None:
    client = make_client(chat_graph())
    used = create_thread(client)
    untouched = create_thread(client)
    assert stream_run(client, used, human_input()).status_code == 200

    body = client.get(f"/threads/{untouched}/state").json()

    assert body["values"].get("messages", []) == []
