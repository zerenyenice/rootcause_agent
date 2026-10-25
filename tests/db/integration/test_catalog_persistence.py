"""Metadata catalog on Postgres + pgvector through the default SQL repositories (RED).

The app connects as the runtime role of a migrated database (``migrated_roles``);
only the embeddings client is replaced (an offline 8-dimensional fake). Raw SQL
touches only ``catalog.tables.connection_id``, ``catalog.embeddings.connection_id``
and ``catalog.embeddings.embedding``, to prove cascades and vector storage.
Agent wiring (Sensitive) is exercised against a real customer Postgres: a verified
read-only login is restricted to the selected tables; an unverified one is refused.
"""

import asyncio
import os
import uuid
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

CONNECTIONS = "/api/v1/connections"
CATALOG = "/api/v1/catalog"
DIMENSIONS = 8


def _sync(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch, migrated_roles: Any, tmp_path: Path) -> str:
    """Env as a deployment sets it (runtime-role URL, Fernet key, 8-dim embeddings)."""
    from rootcause.settings import clear_settings_cache

    for name in ("ROOTCAUSE_MIGRATION_DATABASE_URL", "ROOTCAUSE_ADMIN_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert os.environ["ROOTCAUSE_DATABASE_URL"] == migrated_roles.app_url
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("ROOTCAUSE_SECRET_KEY", key)
    monkeypatch.setenv("ROOTCAUSE_EMBEDDING_DIMENSIONS", str(DIMENSIONS))
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()
    return key


@pytest.fixture
def embedder(make_embeddings: Callable[..., Any]) -> Any:
    return make_embeddings()


@pytest.fixture
def open_client(app_env: str, embedder: Any) -> Iterator[Callable[[], TestClient]]:
    """Factory of TestClients, each over a freshly built app sharing only the database."""
    from rootcause.api.main import create_app
    from rootcause.api.routes.catalog import get_embeddings
    from rootcause.settings import clear_settings_cache

    clients: list[TestClient] = []

    def _open() -> TestClient:
        clear_settings_cache()
        app = create_app()
        app.dependency_overrides[get_embeddings] = lambda: embedder
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield _open
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def admin_engine(migrated_roles: Any) -> Iterator[sa.Engine]:
    engine = sa.create_engine(_sync(migrated_roles.admin_url))
    yield engine
    engine.dispose()


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE plants (plant_id VARCHAR NOT NULL, plant_name VARCHAR)")
        con.execute("INSERT INTO plants VALUES ('P01','Alpha'),('P02','Beta')")
        con.execute("CREATE TABLE production (ts INTEGER, units INTEGER, scrap INTEGER)")
        con.execute("INSERT INTO production SELECT i, i * 10, i FROM range(4) t(i)")
    finally:
        con.close()
    return path


def _create(client: TestClient, body: dict[str, Any]) -> str:
    response = client.post(CONNECTIONS, json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def _duckdb(client: TestClient, path: Path) -> str:
    return _create(client, {"name": "demo", "kind": "duckdb", "path": str(path)})


def _postgres(client: TestClient, customer_db: Any, user: str) -> str:
    return _create(
        client,
        {
            "name": "customer",
            "kind": "postgres",
            "host": customer_db.host,
            "port": customer_db.port,
            "database": customer_db.database,
            "user": user,
            "password": customer_db.password,
            "sslmode": "disable",
        },
    )


def _import(client: TestClient, connection_id: str) -> dict[str, Any]:
    response = client.post(f"{CATALOG}/{connection_id}/import")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _tables(client: TestClient, connection_id: str) -> list[dict[str, Any]]:
    response = client.get(f"{CATALOG}/{connection_id}/tables")
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()["data"]
    return data


def _by_name(tables: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f"{t['schema']}.{t['name']}": t for t in tables}


def _patch(client: TestClient, kind: str, item_id: str, **body: Any) -> dict[str, Any]:
    response = client.patch(f"{CATALOG}/{kind}/{item_id}", json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _verify(client: TestClient, connection_id: str) -> dict[str, Any]:
    response = client.post(f"{CONNECTIONS}/{connection_id}/test")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def _wiring(connection_id: str, embedder: Any) -> Any:
    from rootcause.catalog.agent_tools import build_catalog_wiring
    from rootcause.catalog.repository import SqlCatalogRepository
    from rootcause.connections.repository import SqlConnectionRepository
    from rootcause.db.engine import make_async_engine
    from rootcause.settings import get_settings

    settings = get_settings()
    connections = SqlConnectionRepository(make_async_engine(settings))
    catalog = SqlCatalogRepository(make_async_engine(settings))
    try:
        return await build_catalog_wiring(
            uuid.UUID(connection_id),
            connections=connections,
            catalog=catalog,
            embedder=embedder,
            settings=settings,
        )
    finally:
        await connections.dispose()
        await catalog.dispose()


async def _search(connection_id: str, embedder: Any, query: str, k: int) -> dict[str, Any]:
    """Build the wiring and run ``search_catalog`` in one event loop (engines are loop-bound)."""
    from rootcause.catalog.agent_tools import build_catalog_wiring
    from rootcause.catalog.repository import SqlCatalogRepository
    from rootcause.connections.repository import SqlConnectionRepository
    from rootcause.db.engine import make_async_engine
    from rootcause.settings import get_settings

    settings = get_settings()
    connections = SqlConnectionRepository(make_async_engine(settings))
    catalog = SqlCatalogRepository(make_async_engine(settings))
    try:
        wiring = await build_catalog_wiring(
            uuid.UUID(connection_id),
            connections=connections,
            catalog=catalog,
            embedder=embedder,
            settings=settings,
        )
        wiring.executor.close()
        [search] = [t for t in wiring.tools if t.name == "search_catalog"]
        result: dict[str, Any] = await search.ainvoke({"query": query, "k": k})
        return result
    finally:
        await connections.dispose()
        await catalog.dispose()


# --- persistence -------------------------------------------------------------


def test_import_and_edits_survive_a_new_app_instance(
    open_client: Callable[[], TestClient], warehouse: Path
) -> None:
    client = open_client()
    connection_id = _duckdb(client, warehouse)
    assert _import(client, connection_id) == {"tables": 2, "columns": 5}
    plants = _by_name(_tables(client, connection_id))["main.plants"]
    _patch(client, "tables", plants["id"], selected=True, description="Sites ☃ '; --")
    _patch(client, "columns", plants["columns"][1]["id"], selected=False, description="Name")

    fetched = _by_name(_tables(open_client(), connection_id))

    assert list(fetched) == ["main.plants", "main.production"]
    stored = fetched["main.plants"]
    assert (stored["id"], stored["selected"], stored["description"]) == (
        plants["id"],
        True,
        "Sites ☃ '; --",
    )
    assert [(c["name"], c["selected"], c["description"]) for c in stored["columns"]] == [
        ("plant_id", True, None),
        ("plant_name", False, "Name"),
    ]
    assert stored["row_estimate"] == 2


def test_reimport_keeps_selection_and_removes_vanished_tables(
    open_client: Callable[[], TestClient], warehouse: Path
) -> None:
    client = open_client()
    connection_id = _duckdb(client, warehouse)
    _import(client, connection_id)
    plants = _by_name(_tables(client, connection_id))["main.plants"]
    _patch(client, "tables", plants["id"], selected=True, description="Sites.")
    con = duckdb.connect(str(warehouse))
    con.execute("DROP TABLE production")
    con.execute("CREATE TABLE machines (machine_id VARCHAR)")
    con.close()

    data = _import(open_client(), connection_id)

    assert data == {"tables": 2, "columns": 3}
    tables = _by_name(_tables(open_client(), connection_id))
    assert list(tables) == ["main.machines", "main.plants"]
    assert tables["main.plants"]["id"] == plants["id"]
    assert tables["main.plants"]["description"] == "Sites."
    assert tables["main.plants"]["selected"] is True
    assert tables["main.machines"]["selected"] is False


def test_concurrent_imports_neither_fail_nor_duplicate_rows(
    open_client: Callable[[], TestClient], warehouse: Path, admin_engine: sa.Engine
) -> None:
    connection_id = _duckdb(open_client(), warehouse)
    clients = [open_client() for _ in range(4)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda c: c.post(f"{CATALOG}/{connection_id}/import"), clients))

    assert [r.status_code for r in responses] == [200] * 4
    with admin_engine.connect() as connection:
        count = connection.execute(
            sa.text("SELECT count(*) FROM catalog.tables WHERE connection_id = :id"),
            {"id": uuid.UUID(connection_id)},
        ).scalar_one()
    assert count == 2
    assert sum(len(t["columns"]) for t in _tables(open_client(), connection_id)) == 5


def test_postgres_import_lists_every_schema_sorted_with_column_metadata(
    open_client: Callable[[], TestClient], customer_db: Any
) -> None:
    client = open_client()
    connection_id = _postgres(client, customer_db, customer_db.reader)

    assert _import(client, connection_id) == {"tables": 3, "columns": 6}

    tables = _tables(client, connection_id)
    assert [(t["schema"], t["name"]) for t in tables] == [
        ("public", "big"),
        ("public", "orders"),
        ("sales", "regions"),
    ]
    orders = tables[1]
    assert [(c["name"], c["type"], c["nullable"]) for c in orders["columns"]] == [
        ("id", "integer", False),
        ("amount", "numeric", False),
        ("status", "text", True),
    ]
    assert orders["row_estimate"] == 300


def test_deleting_a_connection_removes_its_catalog_and_embeddings(
    open_client: Callable[[], TestClient], warehouse: Path, admin_engine: sa.Engine
) -> None:
    client = open_client()
    connection_id = _duckdb(client, warehouse)
    _import(client, connection_id)
    for table in _tables(client, connection_id):
        _patch(client, "tables", table["id"], selected=True)
    assert client.post(f"{CATALOG}/{connection_id}/index").json()["data"]["status"] == "done"
    table_id = _tables(client, connection_id)[0]["id"]

    assert client.delete(f"{CONNECTIONS}/{connection_id}").status_code == 200

    with admin_engine.connect() as connection:
        for relation in ("catalog.tables", "catalog.embeddings"):
            count = connection.execute(
                sa.text(f"SELECT count(*) FROM {relation} WHERE connection_id = :id"),  # noqa: S608
                {"id": uuid.UUID(connection_id)},
            ).scalar_one()
            assert count == 0, relation
    assert client.get(f"{CATALOG}/{connection_id}/tables").status_code == 404
    gone = client.patch(f"{CATALOG}/tables/{table_id}", json={"selected": False})
    assert gone.status_code == 404


# --- index + pgvector ----------------------------------------------------------


def test_index_stores_one_vector_per_entry_with_the_configured_dimension(
    open_client: Callable[[], TestClient], warehouse: Path, admin_engine: sa.Engine
) -> None:
    client = open_client()
    connection_id = _duckdb(client, warehouse)
    _import(client, connection_id)
    tables = _by_name(_tables(client, connection_id))
    _patch(client, "tables", tables["main.production"]["id"], selected=True)

    data = client.post(f"{CATALOG}/{connection_id}/index").json()["data"]

    assert data == {"status": "done", "indexed_tables": 1, "indexed_columns": 3}
    assert open_client().get(f"{CATALOG}/{connection_id}/index").json()["data"] == data
    with admin_engine.connect() as connection:
        dims = connection.execute(
            sa.text(
                "SELECT vector_dims(embedding) FROM catalog.embeddings WHERE connection_id = :id"
            ),
            {"id": uuid.UUID(connection_id)},
        ).scalars()
        assert sorted(dims) == [DIMENSIONS] * 4


def test_search_catalog_ranks_by_pgvector_similarity_and_respects_the_selection(
    open_client: Callable[[], TestClient], warehouse: Path, embedder: Any
) -> None:
    client = open_client()
    connection_id = _duckdb(client, warehouse)
    assert _verify(client, connection_id)["read_only"] is True
    _import(client, connection_id)
    tables = _by_name(_tables(client, connection_id))
    production = tables["main.production"]
    _patch(client, "tables", production["id"], selected=True, description="Hourly output.")
    _patch(client, "tables", tables["main.plants"]["id"], selected=True)
    scrap = [c for c in production["columns"] if c["name"] == "scrap"][0]
    _patch(client, "columns", scrap["id"], description="Scrap units, scrap scrap.")
    client.post(f"{CATALOG}/{connection_id}/index")

    hits = _run(_search(connection_id, embedder, "scrap", 3))["results"]

    assert 1 <= len(hits) <= 3
    assert (hits[0]["kind"], hits[0]["table"], hits[0]["column"]) == (
        "column",
        "production",
        "scrap",
    )
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)

    _patch(client, "tables", production["id"], selected=False)
    hidden = _run(_search(connection_id, embedder, "scrap", 20))["results"]
    assert all(hit["table"] != "production" for hit in hidden)


# --- agent wiring on a real customer Postgres (Sensitive) -----------------------


def test_verified_postgres_login_is_restricted_to_the_selected_tables(
    open_client: Callable[[], TestClient], customer_db: Any, embedder: Any
) -> None:
    from rootcause.agents.tools.schema import make_schema_tools
    from rootcause.connections.duckdb_executor import QueryRejected

    client = open_client()
    connection_id = _postgres(client, customer_db, customer_db.reader)
    assert _verify(client, connection_id)["read_only"] is True
    _import(client, connection_id)
    tables = _by_name(_tables(client, connection_id))
    _patch(client, "tables", tables["public.orders"]["id"], selected=True, description="Orders.")

    wiring = _run(_wiring(connection_id, embedder))
    try:
        result = wiring.executor.run("SELECT count(*) AS n FROM public.orders", "count")
        with pytest.raises(QueryRejected) as caught:
            wiring.executor.run("SELECT code FROM sales.regions", "hidden")
        tools = {t.name: t for t in make_schema_tools(wiring.executor, wiring.table_docs)}
        listed = tools["list_tables"].invoke({})
    finally:
        wiring.executor.close()

    assert result.rows == ((300,),)
    assert "unknown_table" in caught.value.codes
    assert [(t["name"], t["description"]) for t in listed["tables"]] == [
        ("public.orders", "Orders.")
    ]


def test_postgres_login_that_failed_verification_is_refused(
    open_client: Callable[[], TestClient], customer_db: Any, embedder: Any
) -> None:
    from rootcause.catalog.agent_tools import ConnectionNotVerified

    client = open_client()
    connection_id = _postgres(client, customer_db, customer_db.writer)
    assert _verify(client, connection_id)["read_only"] is False

    with pytest.raises(ConnectionNotVerified):
        _run(_wiring(connection_id, embedder))


# --- seed command ----------------------------------------------------------------


def test_seed_demo_main_seeds_the_application_database(
    open_client: Callable[[], TestClient], tmp_path: Path
) -> None:
    from rootcause.catalog.seed_demo import DEMO_CONNECTION_NAME, main
    from rootcause.evals.manufacturing import GeneratorConfig, generate
    from rootcause.evals.manufacturing.table_docs import TABLE_DOCS
    from rootcause.evals.manufacturing.writers import write_duckdb

    path = tmp_path / "demo.duckdb"
    write_duckdb(generate(GeneratorConfig(seed=7, days=2)), path)

    assert main(["--path", str(path)]) == 0
    assert main(["--path", str(path)]) == 0  # idempotent

    client = open_client()
    demos = [c for c in client.get(CONNECTIONS).json()["data"] if c["name"] == DEMO_CONNECTION_NAME]
    assert len(demos) == 1
    tables = _tables(client, demos[0]["id"])
    assert sorted(t["name"] for t in tables) == sorted(TABLE_DOCS)
    assert all(t["selected"] for t in tables)
    assert all(t["description"] == TABLE_DOCS[t["name"]].description for t in tables)


def test_seed_demo_main_fails_with_a_non_zero_exit_for_a_missing_file(
    app_env: str, tmp_path: Path
) -> None:
    from rootcause.catalog.seed_demo import main

    assert main(["--path", str(tmp_path / "missing.duckdb")]) != 0
