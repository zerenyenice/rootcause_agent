"""Human approval: the stream surfaces the interrupt, a resume command continues the run."""

from typing import Any

import pytest

from tests.agent_service.conftest import ClientFactory
from tests.agent_service.support import (
    APPROVAL_REQUEST,
    StreamResult,
    approval_graph,
    create_thread,
    human_input,
    last_messages,
    stream_run,
)

pytestmark = pytest.mark.unit

RESUME = {"command": {"resume": {"decisions": [{"type": "approve"}]}}, "input": None}


def _interrupts(result: StreamResult) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for name in ("values", "updates"):
        for data in result.named(name):
            if isinstance(data, dict) and "__interrupt__" in data:
                found.extend(data["__interrupt__"])
    return found


def test_stream_surfaces_the_interrupt_and_ends(make_client: ClientFactory) -> None:
    client = make_client(approval_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    assert result.status_code == 200
    assert result.names[0] == "metadata"
    assert "error" not in result.names
    interrupts = _interrupts(result)
    assert interrupts, f"no __interrupt__ in values/updates: {result.names}"
    for item in interrupts:
        assert item["value"] == APPROVAL_REQUEST
        assert isinstance(item["id"], str) and item["id"]
    assert all(m["type"] != "ai" for m in last_messages(result))


def test_state_reports_the_pending_interrupt(make_client: ClientFactory) -> None:
    client = make_client(approval_graph())
    thread_id = create_thread(client)
    streamed = _interrupts(stream_run(client, thread_id, human_input()))

    body = client.get(f"/threads/{thread_id}/state").json()

    assert body["next"] == ["approve"]
    [pending] = body["interrupts"]
    assert pending["value"] == APPROVAL_REQUEST
    assert pending["id"] == streamed[0]["id"]


def test_resume_command_continues_from_the_checkpoint(make_client: ClientFactory) -> None:
    client = make_client(approval_graph())
    thread_id = create_thread(client)
    stream_run(client, thread_id, human_input())

    result = stream_run(client, thread_id, RESUME)

    assert result.status_code == 200
    assert result.names[0] == "metadata"
    assert result.named("metadata")[0]["thread_id"] == thread_id
    assert "error" not in result.names
    assert _interrupts(result) == []
    messages = last_messages(result)
    assert [m["type"] for m in messages] == ["human", "ai"]
    assert messages[-1]["content"] == "decision: approve"
    state = client.get(f"/threads/{thread_id}/state").json()
    assert state["next"] == []
    assert state["interrupts"] == []
