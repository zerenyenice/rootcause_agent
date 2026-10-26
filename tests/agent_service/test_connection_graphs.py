"""Each run uses the graph of the chat's selected connection (RED, Sensitive).

Seams (``rootcause.agent_service.main.create_app``)::

    create_app(settings, *, graph_factory=None, checkpointer=None,
               connections=None, catalog=None, embedder=None)

- ``graph_factory(checkpointer=...)`` builds the default graph once at startup
  (unchanged); ``graph_factory(checkpointer=..., wiring=CatalogWiring)`` builds the
  graph of one connection, lazily on its first run, in a worker thread, with the
  same checkpointer. The default factory is ``make_service_graph`` (which gains
  ``wiring``). Graphs are cached per connection id for the app's lifetime and their
  executors are closed at shutdown.
- ``connections``/``catalog`` default to the SQL repositories on the application
  database, ``embedder`` to ``rootcause.clients.embeddings.embeddings(settings)``.
- A run with ``context.connection_id`` resolves the wiring with
  ``rootcause.catalog.agent_tools.build_catalog_wiring`` on EVERY run (the stored
  verification is re-checked even when the graph is cached). Failures are reported
  in-band after ``metadata`` as one ``error`` event ``{"error": <code>, "message":
  <generic>}`` (HTTP 200, no graph built): ``connection_not_found`` (unknown id, or
  an id that is not a UUID), ``connection_not_verified`` and ``connection_unavailable``.
- Without ``context.connection_id`` the default graph runs, as before.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from tests.agent_service.conftest import ClientFactory
from tests.agent_service.support import (
    FactorySpy,
    catalog_fixture,
    create_thread,
    human_input,
    last_messages,
    make_settings,
    make_warehouse,
    stream_run,
    with_connection,
)
from tests.conftest import KeywordEmbeddings

pytestmark = pytest.mark.unit


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path: Path = make_warehouse(tmp_path / "warehouse.duckdb")
    return path


@pytest.fixture
def spy() -> FactorySpy:
    return FactorySpy(calls=[])


def _client(make_client: ClientFactory, spy: FactorySpy, fixture: Any) -> Any:
    return make_client(
        spy,
        connections=fixture.connections,
        catalog=fixture.catalog,
        embedder=KeywordEmbeddings(),
    )


def _reply(result: Any) -> str:
    assert result.status_code == 200, result.raw
    content: str = last_messages(result)[-1]["content"]
    return content


def _assert_single_error(result: Any, code: str, *secrets: str) -> None:
    assert result.status_code == 200
    assert result.names == ["metadata", "error"]
    [error] = result.named("error")
    assert set(error) == {"error", "message"}
    assert error["error"] == code
    assert isinstance(error["message"], str) and error["message"]
    for secret in secrets:
        assert secret not in result.raw


# --- verified connection --------------------------------------------------------


def test_run_with_a_verified_connection_streams_from_its_wired_graph(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path
) -> None:
    fixture = catalog_fixture(warehouse, verified=True)
    client = _client(make_client, spy, fixture)
    thread_id = create_thread(client)

    result = stream_run(client, thread_id, with_connection(fixture.connection_id))

    assert _reply(result) == f"graph=wired:plants connection={fixture.connection_id}"
    [wiring] = spy.wired
    assert [t.name for t in wiring.tools] == ["search_catalog"]
    assert spy.calls[-1]["checkpointer"] is spy.calls[0]["checkpointer"]


def test_the_wired_executor_is_restricted_to_the_selected_tables(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path
) -> None:
    from rootcause.connections.duckdb_executor import QueryRejected

    fixture = catalog_fixture(warehouse, verified=True)
    client = _client(make_client, spy, fixture)
    stream_run(client, create_thread(client), with_connection(fixture.connection_id))
    [wiring] = spy.wired

    assert wiring.executor.run("SELECT count(*) AS n FROM plants", "n").rows == ((3,),)
    with pytest.raises(QueryRejected):
        wiring.executor.run("SELECT count(*) AS n FROM production", "hidden")


def test_graphs_are_cached_per_connection(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path
) -> None:
    fixture = catalog_fixture(warehouse, verified=True)
    client = _client(make_client, spy, fixture)

    first = stream_run(client, create_thread(client), with_connection(fixture.connection_id))
    second = stream_run(client, create_thread(client), with_connection(fixture.connection_id))

    assert _reply(first).startswith("graph=wired:plants")
    assert _reply(second).startswith("graph=wired:plants")
    assert len(spy.wired) == 1


def test_different_connections_get_different_graphs(
    make_client: ClientFactory, spy: FactorySpy, tmp_path: Path
) -> None:
    from rootcause.catalog.service import import_catalog
    from rootcause.connections.repository import NewConnection

    fixture = catalog_fixture(make_warehouse(tmp_path / "a.duckdb"), verified=True)
    other_path = make_warehouse(tmp_path / "b.duckdb")

    async def add_other() -> str:
        record = await fixture.connections.create(
            NewConnection(name="other", kind="duckdb", path=str(other_path))
        )
        await import_catalog(
            record.id,
            connections=fixture.connections,
            catalog=fixture.catalog,
            settings=make_settings(),
        )
        for table in await fixture.catalog.list_tables(record.id):
            if table.name == "production":
                await fixture.catalog.update_table(table.id, {"selected": True})
        await fixture.connections.update(record.id, {"read_only_verified_at": datetime.now(UTC)})
        return str(record.id)

    other_id = asyncio.run(add_other())
    client = _client(make_client, spy, fixture)

    first = stream_run(client, create_thread(client), with_connection(fixture.connection_id))
    second = stream_run(client, create_thread(client), with_connection(other_id))

    assert _reply(first).startswith("graph=wired:plants ")
    assert _reply(second).startswith("graph=wired:production ")
    assert len(spy.wired) == 2


def test_cached_graph_is_not_used_once_the_connection_loses_verification(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path
) -> None:
    fixture = catalog_fixture(warehouse, verified=True)
    client = _client(make_client, spy, fixture)
    stream_run(client, create_thread(client), with_connection(fixture.connection_id))
    asyncio.run(
        fixture.connections.update(
            uuid.UUID(fixture.connection_id), {"read_only_verified_at": None}
        )
    )

    result = stream_run(client, create_thread(client), with_connection(fixture.connection_id))

    _assert_single_error(result, "connection_not_verified", str(warehouse))


def test_wired_executors_are_closed_at_shutdown(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path
) -> None:
    from rootcause.agent_service.main import create_app

    fixture = catalog_fixture(warehouse, verified=True)
    app = create_app(
        make_settings(),
        graph_factory=spy,
        checkpointer=InMemorySaver(),
        connections=fixture.connections,
        catalog=fixture.catalog,
        embedder=KeywordEmbeddings(),
    )
    with TestClient(app) as client:
        stream_run(client, create_thread(client), with_connection(fixture.connection_id))
        [wiring] = spy.wired

    with pytest.raises(RuntimeError):
        wiring.executor.run("SELECT 1 AS one FROM plants", "after shutdown")


# --- refused connections -----------------------------------------------------------


def test_run_with_an_unverified_connection_ends_with_connection_not_verified(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path
) -> None:
    fixture = catalog_fixture(warehouse, verified=False)
    client = _client(make_client, spy, fixture)

    result = stream_run(client, create_thread(client), with_connection(fixture.connection_id))

    _assert_single_error(result, "connection_not_verified", str(warehouse), "warehouse.duckdb")
    assert spy.wired == []


@pytest.mark.parametrize("connection_id", ["00000000-0000-4000-8000-000000000000", "c-42"])
def test_run_with_an_unknown_connection_ends_with_connection_not_found(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path, connection_id: str
) -> None:
    fixture = catalog_fixture(warehouse, verified=True)
    client = _client(make_client, spy, fixture)

    result = stream_run(client, create_thread(client), with_connection(connection_id))

    _assert_single_error(result, "connection_not_found")
    assert spy.wired == []


def test_run_with_a_verified_connection_whose_file_vanished_reports_unavailable(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path
) -> None:
    fixture = catalog_fixture(warehouse, verified=True)
    client = _client(make_client, spy, fixture)
    warehouse.unlink()

    result = stream_run(client, create_thread(client), with_connection(fixture.connection_id))

    _assert_single_error(result, "connection_unavailable", str(warehouse), "warehouse.duckdb")
    assert spy.wired == []


def test_thread_stays_usable_after_a_refused_connection(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path
) -> None:
    fixture = catalog_fixture(warehouse, verified=False)
    client = _client(make_client, spy, fixture)
    thread_id = create_thread(client)
    stream_run(client, thread_id, with_connection(fixture.connection_id))

    result = stream_run(client, thread_id, human_input())

    assert _reply(result) == "graph=default connection=None"


# --- no connection: the default graph --------------------------------------------------


@pytest.mark.parametrize(
    "context", [None, {}, {"connection_id": None}], ids=["none", "empty", "null"]
)
def test_run_without_a_connection_uses_the_default_graph(
    make_client: ClientFactory, spy: FactorySpy, warehouse: Path, context: Any
) -> None:
    fixture = catalog_fixture(warehouse, verified=True)
    client = _client(make_client, spy, fixture)
    body = human_input() if context is None else {**human_input(), "context": context}

    result = stream_run(client, create_thread(client), body)

    assert _reply(result) == "graph=default connection=None"
    assert spy.wired == []
    assert len(spy.calls) == 1  # only the startup default graph


def test_default_graph_is_built_once_at_startup_without_wiring(
    make_client: ClientFactory, spy: FactorySpy
) -> None:
    make_client(spy)

    assert len(spy.calls) == 1
    assert spy.calls[0]["wiring"] is None
