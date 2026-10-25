"""Run config: Langfuse tracing per run (session = thread) and connection_id passthrough."""

from pathlib import Path

import pytest

from tests.agent_service.conftest import ClientFactory
from tests.agent_service.support import (
    HandlerSpy,
    catalog_fixture,
    chat_graph,
    connection_echo_graph,
    create_thread,
    human_input,
    last_messages,
    make_settings,
    make_warehouse,
    stream_run,
    with_connection,
)

pytestmark = pytest.mark.unit


def _langfuse_settings() -> object:
    return make_settings(
        langfuse_enabled=True,
        langfuse_host="https://langfuse.example.invalid",
        langfuse_public_key="pk-lf-SENTINEL",  # gitleaks:allow
        langfuse_secret_key="sk-lf-SENTINEL",  # gitleaks:allow
    )


def test_each_run_is_traced_with_the_thread_as_langfuse_session(
    make_client: ClientFactory, langfuse_spy: HandlerSpy
) -> None:
    client = make_client(chat_graph(), _langfuse_settings())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    assert result.status_code == 200
    assert len(langfuse_spy.calls) == 1
    [handler] = langfuse_spy.handlers
    assert handler.root_metadata, "the Langfuse handler was not attached to the run"
    assert handler.root_metadata[0]["langfuse_session_id"] == thread_id


def test_every_run_gets_a_fresh_langfuse_handler(
    make_client: ClientFactory, langfuse_spy: HandlerSpy
) -> None:
    client = make_client(connection_echo_graph(), _langfuse_settings())
    thread_id = create_thread(client)

    stream_run(client, thread_id, human_input("first"))
    stream_run(client, thread_id, human_input("second"))

    assert len(langfuse_spy.calls) == 2
    assert all(handler.root_metadata for handler in langfuse_spy.handlers)


def test_langfuse_is_not_touched_when_disabled(
    make_client: ClientFactory, langfuse_spy: HandlerSpy
) -> None:
    client = make_client(chat_graph(), make_settings(langfuse_enabled=False))
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, human_input())

    assert result.status_code == 200
    assert langfuse_spy.calls == []


def test_context_connection_id_reaches_the_graph_config(
    make_client: ClientFactory, tmp_path: Path
) -> None:
    # Spec change (feat/catalog): a connection id must name a verified stored
    # connection; the per-connection graph still sees it in its config.
    fixture = catalog_fixture(make_warehouse(tmp_path / "w.duckdb"), verified=True)
    client = make_client(
        connection_echo_graph(), connections=fixture.connections, catalog=fixture.catalog
    )
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, with_connection(fixture.connection_id))

    assert last_messages(result)[-1]["content"] == f"connection={fixture.connection_id}"


def test_connection_id_is_absent_without_context(make_client: ClientFactory) -> None:
    client = make_client(connection_echo_graph())
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, {**human_input(), "context": None})

    assert last_messages(result)[-1]["content"] == "connection=None"
