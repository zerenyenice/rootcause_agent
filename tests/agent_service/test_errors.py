"""A failing run ends the stream with a generic error event that leaks nothing."""

import pytest

from tests.agent_service.conftest import ClientFactory
from tests.agent_service.support import create_thread, failing_graph, human_input, stream_run

pytestmark = pytest.mark.unit


def test_model_failure_becomes_a_generic_error_event(make_client: ClientFactory) -> None:
    client = make_client(failing_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    assert result.status_code == 200
    assert result.names[0] == "metadata"
    assert result.names[-1] == "error"
    [error] = result.named("error")
    assert set(error) == {"error", "message"}
    assert isinstance(error["error"], str) and error["error"]
    assert isinstance(error["message"], str) and error["message"]
    assert "SENTINEL" not in result.raw
    assert "postgres://" not in result.raw


def test_thread_stays_usable_after_a_failed_run(make_client: ClientFactory) -> None:
    client = make_client(failing_graph())
    thread_id = create_thread(client)
    stream_run(client, thread_id, human_input())

    response = client.get(f"/threads/{thread_id}/state")

    assert response.status_code == 200
    assert "SENTINEL" not in response.text
