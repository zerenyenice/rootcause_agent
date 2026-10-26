"""Agent wiring over the catalog: verified executors, selection-restricted tools (RED, Sensitive).

``rootcause.catalog.agent_tools`` turns a stored connection into what the
orchestrator needs:

- ``open_connection_executor`` returns the executor for a connection only when it
  was verified read-only (``read_only_verified_at`` set); otherwise it raises
  ``ConnectionNotVerified`` *before* opening anything.
- ``build_catalog_wiring`` restricts that executor to the catalog's SELECTED tables
  and columns: ``list_tables``/``describe_table``/``column_values`` see only those,
  with the catalog descriptions as ``table_docs``, and ``run_sql``/``explain_sql``
  reject any statement that touches an unselected table or names an unselected
  column (validated against a ``CatalogSnapshot`` of the selection before the
  inner executor sees it). Its ``tools`` hold ``search_catalog``.

Everything runs offline over a small DuckDB file and in-memory repositories.
"""

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

SENTINEL_PASSWORD = "SENTINEL-catalog-pw-1"  # gitleaks:allow
SENTINEL_ERROR = "SENTINEL-embedding-failure-detail"
NON_ROUTABLE_HOST = "10.255.255.1"
UNKNOWN_MESSAGE_FRAGMENT = "Unknown table or column"


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


async def _duckdb(connections: Any, path: Path, *, verified: bool) -> uuid.UUID:
    from rootcause.connections.repository import NewConnection

    record = await connections.create(NewConnection(name="demo", kind="duckdb", path=str(path)))
    connection_id: uuid.UUID = record.id
    if verified:
        await connections.update(connection_id, {"read_only_verified_at": datetime.now(UTC)})
    return connection_id


async def _postgres(
    connections: Any, settings: Any, *, host: str, port: int, verified: bool
) -> uuid.UUID:
    from rootcause.connections.repository import NewConnection
    from rootcause.connections.secrets import cipher_from_settings

    secret = cipher_from_settings(settings).encrypt(SENTINEL_PASSWORD)
    new = NewConnection(
        name="pg",
        kind="postgres",
        host=host,
        port=port,
        database="warehouse",
        user="analyst",
        sslmode="disable",
        encrypted_secret=secret,
    )
    record = await connections.create(new)
    connection_id: uuid.UUID = record.id
    if verified:
        await connections.update(connection_id, {"read_only_verified_at": datetime.now(UTC)})
    return connection_id


async def _select(
    catalog: Any,
    connection_id: uuid.UUID,
    *,
    tables: dict[str, dict[str, Any]],
    columns: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> None:
    """Apply table and (table, column) changes to the imported catalog."""
    for table in await catalog.list_tables(connection_id):
        if table.name in tables:
            assert await catalog.update_table(table.id, tables[table.name]) is not None
        for column in table.columns:
            change = (columns or {}).get((table.name, column.name))
            if change is not None:
                assert await catalog.update_column(column.id, change) is not None


@pytest.fixture
def selected(connections: Any, catalog: Any, settings: Any, warehouse: Path) -> uuid.UUID:
    """Verified DuckDB: ``plants`` selected minus ``plant_name``; ``production`` unselected."""
    from rootcause.catalog.service import import_catalog

    async def scenario() -> uuid.UUID:
        connection_id = await _duckdb(connections, warehouse, verified=True)
        await import_catalog(
            connection_id, connections=connections, catalog=catalog, settings=settings
        )
        await _select(
            catalog,
            connection_id,
            tables={"plants": {"selected": True, "description": "Manufacturing sites."}},
            columns={
                ("plants", "plant_id"): {"description": "Plant identifier."},
                ("plants", "plant_name"): {"selected": False},
            },
        )
        return connection_id

    return _run(scenario())


@pytest.fixture
def wiring(
    selected: uuid.UUID, connections: Any, catalog: Any, embedder: Any, settings: Any
) -> Any:
    from rootcause.catalog.agent_tools import build_catalog_wiring

    built = _run(
        build_catalog_wiring(
            selected,
            connections=connections,
            catalog=catalog,
            embedder=embedder,
            settings=settings,
        )
    )
    yield built
    built.executor.close()


def _schema_tools(wiring: Any) -> dict[str, Any]:
    from rootcause.agents.tools.schema import make_schema_tools

    return {t.name: t for t in make_schema_tools(wiring.executor, wiring.table_docs)}


# --- open_connection_executor --------------------------------------------------


def test_unverified_duckdb_connection_is_refused_before_the_file_is_opened(
    connections: Any, settings: Any, tmp_path: Path
) -> None:
    from rootcause.catalog.agent_tools import ConnectionNotVerified, open_connection_executor

    missing = tmp_path / "missing.duckdb"  # opening it would raise ConnectionUnavailable
    connection_id = _run(_duckdb(connections, missing, verified=False))

    with pytest.raises(ConnectionNotVerified) as caught:
        _run(open_connection_executor(connection_id, connections=connections, settings=settings))

    assert "not verified read-only" in str(caught.value)
    assert caught.value.code == "connection_not_verified"
    assert str(missing) not in str(caught.value)


def test_unverified_postgres_connection_is_refused_without_a_login_attempt(
    connections: Any, settings: Any
) -> None:
    from rootcause.catalog.agent_tools import ConnectionNotVerified, open_connection_executor

    connection_id = _run(
        _postgres(connections, settings, host=NON_ROUTABLE_HOST, port=5432, verified=False)
    )
    started = time.monotonic()

    with pytest.raises(ConnectionNotVerified):
        _run(open_connection_executor(connection_id, connections=connections, settings=settings))

    assert time.monotonic() - started < 1.0  # a connect attempt would wait for the timeout


def test_verified_duckdb_connection_returns_a_working_read_only_executor(
    connections: Any, settings: Any, warehouse: Path
) -> None:
    from rootcause.catalog.agent_tools import open_connection_executor
    from rootcause.connections.duckdb_executor import DuckDBExecutor

    connection_id = _run(_duckdb(connections, warehouse, verified=True))
    before = warehouse.read_bytes()

    executor = _run(
        open_connection_executor(connection_id, connections=connections, settings=settings)
    )
    try:
        assert isinstance(executor, DuckDBExecutor)
        result = executor.run("SELECT count(*) AS n FROM plants", "count plants")
        assert result.rows == ((3,),)
    finally:
        executor.close()
    assert warehouse.read_bytes() == before


def test_verification_cleared_after_a_login_change_refuses_again(
    connections: Any, settings: Any, warehouse: Path
) -> None:
    from rootcause.catalog.agent_tools import ConnectionNotVerified, open_connection_executor

    connection_id = _run(_duckdb(connections, warehouse, verified=True))
    _run(connections.update(connection_id, {"read_only_verified_at": None}))

    with pytest.raises(ConnectionNotVerified):
        _run(open_connection_executor(connection_id, connections=connections, settings=settings))


def test_unknown_connection_raises_connection_not_found(connections: Any, settings: Any) -> None:
    from rootcause.catalog.agent_tools import open_connection_executor
    from rootcause.errors import RootCauseError

    with pytest.raises(RootCauseError) as caught:
        _run(open_connection_executor(uuid.uuid4(), connections=connections, settings=settings))

    assert caught.value.code == "connection_not_found"


def test_verified_but_unreachable_postgres_raises_a_generic_unavailable_error(
    connections: Any, settings: Any
) -> None:
    from rootcause.catalog.agent_tools import open_connection_executor
    from rootcause.connections.probe import ConnectionUnavailable

    connection_id = _run(_postgres(connections, settings, host="127.0.0.1", port=1, verified=True))

    with pytest.raises(ConnectionUnavailable) as caught:
        _run(open_connection_executor(connection_id, connections=connections, settings=settings))

    assert SENTINEL_PASSWORD not in str(caught.value)
    assert "127.0.0.1" not in str(caught.value)


def test_verified_duckdb_connection_whose_file_vanished_raises_unavailable(
    connections: Any, settings: Any, warehouse: Path
) -> None:
    from rootcause.catalog.agent_tools import open_connection_executor
    from rootcause.connections.probe import ConnectionUnavailable

    connection_id = _run(_duckdb(connections, warehouse, verified=True))
    warehouse.unlink()

    with pytest.raises(ConnectionUnavailable):
        _run(open_connection_executor(connection_id, connections=connections, settings=settings))


# --- build_catalog_wiring: refusal ---------------------------------------------


def test_wiring_refuses_an_unverified_connection(
    connections: Any, catalog: Any, embedder: Any, settings: Any, warehouse: Path
) -> None:
    from rootcause.catalog.agent_tools import ConnectionNotVerified, build_catalog_wiring

    connection_id = _run(_duckdb(connections, warehouse, verified=False))

    with pytest.raises(ConnectionNotVerified):
        _run(
            build_catalog_wiring(
                connection_id,
                connections=connections,
                catalog=catalog,
                embedder=embedder,
                settings=settings,
            )
        )


# --- schema tools see only the selection -----------------------------------------


def test_wiring_executor_supports_the_sql_and_schema_tools(wiring: Any) -> None:
    from rootcause.agents.tools.schema import SchemaExecutor

    executor = wiring.executor
    assert isinstance(executor, SchemaExecutor)
    assert callable(getattr(executor, "run", None))
    assert callable(getattr(executor, "explain", None))
    assert [t.name for t in wiring.tools] == ["search_catalog"]


def test_list_tables_lists_only_selected_tables_with_their_descriptions(wiring: Any) -> None:
    result = _schema_tools(wiring)["list_tables"].invoke({})

    assert [(t["name"], t["description"]) for t in result["tables"]] == [
        ("plants", "Manufacturing sites.")
    ]
    assert result["tables"][0]["row_count"] == 3


def test_describe_table_lists_only_selected_columns_with_descriptions(wiring: Any) -> None:
    result = _schema_tools(wiring)["describe_table"].invoke({"table": "plants"})

    assert result["description"] == "Manufacturing sites."
    assert result["columns"] == [
        {"name": "plant_id", "type": "VARCHAR", "description": "Plant identifier."}
    ]


@pytest.mark.parametrize("table", ["production", "main.production", "PRODUCTION", "nope"])
def test_describe_table_treats_an_unselected_table_as_unknown(wiring: Any, table: str) -> None:
    result = _schema_tools(wiring)["describe_table"].invoke({"table": table})

    assert set(result) == {"error"}
    assert UNKNOWN_MESSAGE_FRAGMENT in result["error"]


def test_column_values_of_an_unselected_column_or_table_is_unknown(wiring: Any) -> None:
    tool = _schema_tools(wiring)["column_values"]

    hidden_column = tool.invoke({"table": "plants", "column": "plant_name"})
    hidden_table = tool.invoke({"table": "production", "column": "units"})
    visible = tool.invoke({"table": "plants", "column": "plant_id"})

    assert UNKNOWN_MESSAGE_FRAGMENT in hidden_column["error"]
    assert UNKNOWN_MESSAGE_FRAGMENT in hidden_table["error"]
    assert visible["values"] == ["P01", "P02", "P03"]


def test_a_table_with_no_selected_columns_is_listed_but_shows_no_columns(
    selected: uuid.UUID, connections: Any, catalog: Any, embedder: Any, settings: Any
) -> None:
    from rootcause.catalog.agent_tools import build_catalog_wiring

    _run(
        _select(catalog, selected, tables={}, columns={("plants", "plant_id"): {"selected": False}})
    )
    wiring = _run(
        build_catalog_wiring(
            selected, connections=connections, catalog=catalog, embedder=embedder, settings=settings
        )
    )
    try:
        result = _schema_tools(wiring)["describe_table"].invoke({"table": "plants"})
    finally:
        wiring.executor.close()

    assert result["columns"] == []


def test_wiring_reflects_the_selection_at_build_time_only(
    selected: uuid.UUID, connections: Any, catalog: Any, embedder: Any, settings: Any
) -> None:
    from rootcause.catalog.agent_tools import build_catalog_wiring

    _run(_select(catalog, selected, tables={"production": {"selected": True}}))
    wiring = _run(
        build_catalog_wiring(
            selected, connections=connections, catalog=catalog, embedder=embedder, settings=settings
        )
    )
    try:
        names = [t["name"] for t in _schema_tools(wiring)["list_tables"].invoke({})["tables"]]
        result = wiring.executor.run("SELECT sum(units) AS total FROM production", "total")
    finally:
        wiring.executor.close()

    assert names == ["plants", "production"]
    assert result.rows == ((100,),)


# --- run_sql / explain_sql are restricted to the selection ------------------------


def test_run_sql_on_selected_table_and_columns_returns_rows(wiring: Any) -> None:
    result = wiring.executor.run("SELECT plant_id FROM plants ORDER BY plant_id", "ids")

    assert result.rows == (("P01",), ("P02",), ("P03",))


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT count(*) AS n FROM production", "unknown_table"),
        ("SELECT count(*) AS n FROM main.production", "unknown_table"),
        ("SELECT p.plant_id FROM plants p JOIN production x ON x.ts = 1", "unknown_table"),
        (
            "SELECT plant_id FROM plants WHERE plant_id IN "
            "(SELECT CAST(ts AS VARCHAR) FROM production)",
            "unknown_table",
        ),
        ("WITH x AS (SELECT * FROM production) SELECT * FROM x", "unknown_table"),
        (
            "SELECT plant_id FROM plants UNION ALL SELECT CAST(ts AS VARCHAR) FROM production",
            "unknown_table",
        ),
        ("SELECT plant_name FROM plants", "unknown_column"),
        ("SELECT p.plant_name FROM plants AS p", "unknown_column"),
    ],
    ids=[
        "bare",
        "qualified",
        "join",
        "subquery",
        "cte",
        "union",
        "unselected-column",
        "unselected-column-aliased",
    ],
)
def test_run_sql_rejects_statements_touching_the_unselected_catalog(
    wiring: Any, sql: str, code: str
) -> None:
    from rootcause.connections.duckdb_executor import QueryRejected

    with pytest.raises(QueryRejected) as caught:
        wiring.executor.run(sql, "probe hidden data")

    assert code in caught.value.codes


def test_explain_sql_rejects_an_unselected_table(wiring: Any) -> None:
    from rootcause.connections.duckdb_executor import QueryRejected

    with pytest.raises(QueryRejected) as caught:
        wiring.executor.explain("SELECT * FROM production")

    assert "unknown_table" in caught.value.codes


def test_explain_sql_on_the_selection_returns_an_estimate(wiring: Any) -> None:
    estimate = wiring.executor.explain("SELECT plant_id FROM plants")

    assert estimate.estimated_rows >= 1


def test_run_sql_through_the_agent_tool_returns_an_error_payload_for_hidden_tables(
    wiring: Any,
) -> None:
    from langchain_core.messages import ToolMessage

    from rootcause.agents.tools.sql import ApprovalRegistry, make_sql_tools

    tools = {t.name: t for t in make_sql_tools(wiring.executor, 1_000_000, ApprovalRegistry())}
    call = {
        "name": "run_sql",
        "args": {"sql": "SELECT * FROM production", "purpose": "probe"},
        "id": "call_hidden",
        "type": "tool_call",
    }

    output = tools["run_sql"].invoke(call, config={"configurable": {"thread_id": "t"}})

    message = output if isinstance(output, ToolMessage) else output.update["messages"][-1]
    assert "unknown_table" in str(message.content)
    assert "QueryRejected" in str(message.content)


# --- search_catalog ----------------------------------------------------------------


@pytest.fixture
def indexed(
    selected: uuid.UUID, connections: Any, catalog: Any, embedder: Any, settings: Any
) -> uuid.UUID:
    """Both tables selected; ``production.scrap`` described as scrap; index built."""
    from rootcause.catalog.service import build_index

    async def scenario() -> uuid.UUID:
        await _select(
            catalog,
            selected,
            tables={"production": {"selected": True, "description": "Hourly output per line."}},
            columns={("production", "scrap"): {"description": "Scrap units, scrap scrap."}},
        )
        status = await build_index(
            selected, connections=connections, catalog=catalog, embedder=embedder, settings=settings
        )
        assert status.status == "done"
        return selected

    return _run(scenario())


def _search_tool(
    connection_id: uuid.UUID, connections: Any, catalog: Any, embedder: Any, settings: Any
) -> Any:
    from rootcause.catalog.agent_tools import build_catalog_wiring

    wiring = _run(
        build_catalog_wiring(
            connection_id,
            connections=connections,
            catalog=catalog,
            embedder=embedder,
            settings=settings,
        )
    )
    wiring.executor.close()
    tools = {t.name: t for t in wiring.tools}
    return tools["search_catalog"]


@pytest.fixture
def search(
    indexed: uuid.UUID, connections: Any, catalog: Any, embedder: Any, settings: Any
) -> Callable[..., dict[str, Any]]:
    tool = _search_tool(indexed, connections, catalog, embedder, settings)

    def _search(**args: Any) -> dict[str, Any]:
        result: dict[str, Any] = _run(tool.ainvoke(args))
        return result

    return _search


def test_search_returns_the_most_similar_catalog_entries_first(search: Any) -> None:
    result = search(query="scrap", k=3)

    hits = result["results"]
    assert 1 <= len(hits) <= 3
    assert hits[0] == {
        "kind": "column",
        "schema": "main",
        "table": "production",
        "column": "scrap",
        "description": "Scrap units, scrap scrap.",
        "score": hits[0]["score"],
    }
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(-1.0 <= score <= 1.0 for score in scores)


def test_search_table_hits_carry_no_column(search: Any) -> None:
    hits = search(query="plant", k=10)["results"]

    tables = [hit for hit in hits if hit["kind"] == "table"]
    assert tables
    assert all(hit["column"] is None for hit in tables)
    assert {hit["table"] for hit in tables} <= {"plants", "production"}


def test_search_never_returns_unselected_columns(search: Any) -> None:
    hits = search(query="plant", k=20)["results"]

    assert ("plants", "plant_name") not in {(h["table"], h["column"]) for h in hits}


def test_search_excludes_entries_deselected_after_indexing(
    indexed: uuid.UUID, connections: Any, catalog: Any, embedder: Any, settings: Any
) -> None:
    _run(_select(catalog, indexed, tables={"production": {"selected": False}}))
    tool = _search_tool(indexed, connections, catalog, embedder, settings)

    hits = _run(tool.ainvoke({"query": "scrap", "k": 20}))["results"]

    assert all(hit["table"] != "production" for hit in hits)


@pytest.mark.parametrize(("k", "most"), [(0, 1), (-5, 1), (1, 1), (1000, 20)])
def test_search_clamps_k_to_between_1_and_20(search: Any, k: int, most: int) -> None:
    hits = search(query="plant", k=k)["results"]

    assert 1 <= len(hits) <= most


def test_search_defaults_k_to_five(search: Any) -> None:
    hits = search(query="plant")["results"]

    assert 1 <= len(hits) <= 5


@pytest.mark.parametrize("query", ["", "   "])
def test_search_with_a_blank_query_returns_an_error_payload(search: Any, query: str) -> None:
    result = search(query=query, k=3)

    assert set(result) == {"error"}


def test_search_failure_returns_a_generic_error_without_details(
    indexed: uuid.UUID,
    connections: Any,
    catalog: Any,
    embedder: Any,
    settings: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool = _search_tool(indexed, connections, catalog, embedder, settings)
    embedder.error = RuntimeError(SENTINEL_ERROR)

    result = _run(tool.ainvoke({"query": "scrap", "k": 3}))

    assert set(result) == {"error"}
    assert SENTINEL_ERROR not in str(result)
    captured = capsys.readouterr()
    assert SENTINEL_ERROR not in captured.out + captured.err


def test_search_before_any_index_returns_no_results(
    selected: uuid.UUID, connections: Any, catalog: Any, embedder: Any, settings: Any
) -> None:
    tool = _search_tool(selected, connections, catalog, embedder, settings)

    assert _run(tool.ainvoke({"query": "plant", "k": 5})) == {"results": []}


def test_search_is_scoped_to_its_connection(
    indexed: uuid.UUID,
    connections: Any,
    catalog: Any,
    embedder: Any,
    settings: Any,
    tmp_path: Path,
) -> None:
    import duckdb

    from rootcause.catalog.service import build_index, import_catalog

    other_path = tmp_path / "other.duckdb"
    con = duckdb.connect(str(other_path))
    con.execute("CREATE TABLE scrap_log (scrap_reason VARCHAR)")
    con.close()

    async def scenario() -> uuid.UUID:
        other = await _duckdb(connections, other_path, verified=True)
        await import_catalog(other, connections=connections, catalog=catalog, settings=settings)
        await _select(catalog, other, tables={"scrap_log": {"selected": True}})
        await build_index(
            other, connections=connections, catalog=catalog, embedder=embedder, settings=settings
        )
        return other

    _run(scenario())
    tool = _search_tool(indexed, connections, catalog, embedder, settings)

    hits = _run(tool.ainvoke({"query": "scrap", "k": 20}))["results"]

    assert all(hit["table"] != "scrap_log" for hit in hits)
