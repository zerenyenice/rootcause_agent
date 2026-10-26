"""Graph cache keyed by connection + login/selection fingerprint (Sensitive).

Editing a description or the selection rebuilds the graph on the next run (the open
executor is kept); a login change replaces the executor and closes the old one.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.agent_service.conftest import ClientFactory
from tests.agent_service.support import (
    FactorySpy,
    catalog_fixture,
    create_thread,
    last_messages,
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


def _run(client: Any, connection_id: str) -> str:
    result = stream_run(client, create_thread(client), with_connection(connection_id))
    assert result.status_code == 200, result.raw
    content: str = last_messages(result)[-1]["content"]
    return content


def _table(fixture: Any, name: str) -> Any:
    tables = asyncio.run(fixture.catalog.list_tables(uuid.UUID(fixture.connection_id)))
    return next(t for t in tables if t.name == name)


def _edit_table(fixture: Any, name: str, changes: dict[str, Any]) -> None:
    asyncio.run(fixture.catalog.update_table(_table(fixture, name).id, changes))


@pytest.fixture
def setup(make_client: ClientFactory, spy: FactorySpy, warehouse: Path) -> tuple[Any, Any]:
    fixture = catalog_fixture(warehouse, verified=True)
    client = make_client(
        spy,
        connections=fixture.connections,
        catalog=fixture.catalog,
        embedder=KeywordEmbeddings(),
    )
    return fixture, client


def test_unchanged_catalog_builds_the_graph_once(setup: tuple[Any, Any], spy: FactorySpy) -> None:
    fixture, client = setup

    _run(client, fixture.connection_id)
    _run(client, fixture.connection_id)
    _run(client, fixture.connection_id)

    assert len(spy.wired) == 1


def test_editing_a_description_rebuilds_and_the_next_run_sees_it(
    setup: tuple[Any, Any], spy: FactorySpy
) -> None:
    fixture, client = setup
    _run(client, fixture.connection_id)
    first_docs = spy.wired[0].table_docs["plants"].description

    _edit_table(fixture, "plants", {"description": "Plants of the group, one row each"})
    _run(client, fixture.connection_id)

    assert len(spy.wired) == 2
    assert spy.wired[1].table_docs["plants"].description == "Plants of the group, one row each"
    assert spy.wired[1].table_docs["plants"].description != first_docs


def test_editing_the_selection_rebuilds_and_the_next_run_sees_it(
    setup: tuple[Any, Any], spy: FactorySpy
) -> None:
    fixture, client = setup
    assert _run(client, fixture.connection_id).startswith("graph=wired:plants ")

    _edit_table(fixture, "production", {"selected": True})
    reply = _run(client, fixture.connection_id)

    assert len(spy.wired) == 2
    assert reply.startswith("graph=wired:plants,production ")
    assert set(spy.wired[1].table_docs) == {"plants", "production"}


def test_a_column_selection_edit_rebuilds(setup: tuple[Any, Any], spy: FactorySpy) -> None:
    fixture, client = setup
    _run(client, fixture.connection_id)
    column = next(c for c in _table(fixture, "plants").columns if c.name == "plant_name")

    asyncio.run(fixture.catalog.update_column(column.id, {"selected": False}))
    _run(client, fixture.connection_id)

    assert len(spy.wired) == 2


def test_the_executor_is_reused_across_a_selection_only_rebuild(
    setup: tuple[Any, Any], spy: FactorySpy
) -> None:
    fixture, client = setup
    _run(client, fixture.connection_id)
    _edit_table(fixture, "production", {"selected": True})
    _run(client, fixture.connection_id)

    old, new = (w.executor for w in spy.wired)

    assert old is not new  # a fresh restriction over the selection ...
    assert old.tables != new.tables
    # ... around the same open connection, which is still usable.
    assert old._inner is new._inner  # noqa: SLF001
    assert old.run("SELECT count(*) AS n FROM plants", "still open").rows == ((3,),)


def test_a_login_change_replaces_the_executor_and_closes_the_old_one(
    setup: tuple[Any, Any], spy: FactorySpy, tmp_path: Path
) -> None:
    fixture, client = setup
    _run(client, fixture.connection_id)
    other = make_warehouse(tmp_path / "moved.duckdb")

    asyncio.run(fixture.connections.update(uuid.UUID(fixture.connection_id), {"path": str(other)}))
    _run(client, fixture.connection_id)

    old, new = (w.executor for w in spy.wired)
    assert old._inner is not new._inner  # noqa: SLF001
    with pytest.raises(RuntimeError):
        old.run("SELECT count(*) AS n FROM plants", "closed")
    assert new.run("SELECT count(*) AS n FROM plants", "open").rows == ((3,),)


def test_a_login_change_to_an_unopenable_source_reports_unavailable(
    setup: tuple[Any, Any], spy: FactorySpy, tmp_path: Path
) -> None:
    fixture, client = setup
    _run(client, fixture.connection_id)
    missing = tmp_path / "gone.duckdb"

    asyncio.run(
        fixture.connections.update(uuid.UUID(fixture.connection_id), {"path": str(missing)})
    )
    result = stream_run(client, create_thread(client), with_connection(fixture.connection_id))

    assert result.names == ["metadata", "error"]
    assert result.named("error")[0]["error"] == "connection_unavailable"
    assert len(spy.wired) == 1


def test_login_fingerprint_changes_with_the_password_but_not_with_the_selection() -> None:
    import dataclasses
    from datetime import UTC, datetime

    from rootcause.catalog.agent_tools import CatalogSelection
    from rootcause.connections.repository import ConnectionRecord

    now = datetime.now(UTC)
    record = ConnectionRecord(
        id=uuid.uuid4(),
        name="pg",
        kind="postgres",
        host="db",
        port=5432,
        database="d",
        user="u",
        sslmode="prefer",
        path=None,
        encrypted_secret=b"one",
        read_only_verified_at=now,
        created_at=now,
        updated_at=now,
    )
    base = CatalogSelection(record, ())
    repassworded = CatalogSelection(dataclasses.replace(record, encrypted_secret=b"two"), ())
    renamed = CatalogSelection(dataclasses.replace(record, name="renamed"), ())

    assert base.login_fingerprint != repassworded.login_fingerprint
    assert base.fingerprint != repassworded.fingerprint
    assert base.login_fingerprint == renamed.login_fingerprint
