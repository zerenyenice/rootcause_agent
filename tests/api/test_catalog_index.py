"""``POST/GET /api/v1/catalog/{connection_id}/index``: vector index of the selected catalog (RED).

Indexing is synchronous for the MVP: the POST embeds and stores everything before it
answers, so its status is ``done`` or ``failed`` (``running`` is never observed). The
GET reports the index currently in effect: ``idle`` with zero counts before the first
successful run, otherwise ``done`` with the stored counts. A failed run answers 200
with ``{"status": "failed", "indexed_tables": 0, "indexed_columns": 0}`` and leaves the
previous index untouched (nothing partial is written).

One embedding per selected table and one per selected column of a selected table.
The texts carry ``schema.table``, the column name and type and the descriptions; the
embeddings client is a deterministic offline fake (``tests/conftest.py``).
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

CONNECTIONS = "/api/v1/connections"
CATALOG = "/api/v1/catalog"
STATUS_FIELDS = {"status", "indexed_tables", "indexed_columns"}
IDLE = {"status": "idle", "indexed_tables": 0, "indexed_columns": 0}
FAILED = {"status": "failed", "indexed_tables": 0, "indexed_columns": 0}
SENTINEL_ERROR = "SENTINEL-embedding-provider-said-no"


def _duckdb(client: TestClient, path: Path) -> str:
    response = client.post(CONNECTIONS, json={"name": "demo", "kind": "duckdb", "path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def _tables(client: TestClient, connection_id: str) -> list[dict[str, Any]]:
    response = client.get(f"{CATALOG}/{connection_id}/tables")
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()["data"]
    return data


def _patch(client: TestClient, kind: str, item_id: str, **body: Any) -> None:
    response = client.patch(f"{CATALOG}/{kind}/{item_id}", json=body)
    assert response.status_code == 200, response.text


def _post_index(client: TestClient, connection_id: str) -> dict[str, Any]:
    response = client.post(f"{CATALOG}/{connection_id}/index")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    data: dict[str, Any] = body["data"]
    assert set(data) == STATUS_FIELDS
    return data


def _get_index(client: TestClient, connection_id: str) -> dict[str, Any]:
    response = client.get(f"{CATALOG}/{connection_id}/index")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


@pytest.fixture
def connection_id(catalog_client: TestClient, warehouse_file: Path) -> str:
    """Imported: ``plants`` selected and described (minus ``plant_name``); ``production`` not."""
    created = _duckdb(catalog_client, warehouse_file)
    assert catalog_client.post(f"{CATALOG}/{created}/import").status_code == 200
    tables = {t["name"]: t for t in _tables(catalog_client, created)}
    plants = tables["plants"]
    _patch(catalog_client, "tables", plants["id"], selected=True, description="Scrap sites.")
    columns = {c["name"]: c for c in plants["columns"]}
    _patch(catalog_client, "columns", columns["plant_id"]["id"], description="Plant identifier.")
    _patch(catalog_client, "columns", columns["plant_name"]["id"], selected=False)
    return created


def test_status_before_any_index_is_idle(catalog_client: TestClient, connection_id: str) -> None:
    assert _get_index(catalog_client, connection_id) == IDLE


def test_index_embeds_selected_tables_and_their_selected_columns(
    catalog_client: TestClient, connection_id: str, catalog_embeddings: Any
) -> None:
    data = _post_index(catalog_client, connection_id)

    assert data == {"status": "done", "indexed_tables": 1, "indexed_columns": 1}
    texts = catalog_embeddings.embedded_texts
    assert len(texts) == 2
    assert all("main.plants" in text for text in texts)
    assert any("Scrap sites." in text for text in texts)
    column_texts = [text for text in texts if "plant_id" in text]
    assert len(column_texts) == 1
    assert "VARCHAR" in column_texts[0]
    assert "Plant identifier." in column_texts[0]
    joined = "\n".join(texts)
    assert "production" not in joined  # unselected table
    assert "plant_name" not in joined  # unselected column


def test_columns_of_an_unselected_table_are_not_indexed(
    catalog_client: TestClient, connection_id: str, catalog_embeddings: Any
) -> None:
    production = {t["name"]: t for t in _tables(catalog_client, connection_id)}["production"]
    assert all(column["selected"] for column in production["columns"])

    data = _post_index(catalog_client, connection_id)

    assert data["indexed_columns"] == 1
    assert not any("units" in text for text in catalog_embeddings.embedded_texts)


def test_status_after_a_successful_index_reports_the_stored_counts(
    catalog_client: TestClient, connection_id: str
) -> None:
    posted = _post_index(catalog_client, connection_id)

    assert _get_index(catalog_client, connection_id) == posted


def test_index_with_nothing_selected_is_done_with_zero_counts(
    catalog_client: TestClient, warehouse_file: Path
) -> None:
    created = _duckdb(catalog_client, warehouse_file)
    catalog_client.post(f"{CATALOG}/{created}/import")

    data = _post_index(catalog_client, created)

    assert data == {"status": "done", "indexed_tables": 0, "indexed_columns": 0}


def test_reindex_replaces_the_previous_index(
    catalog_client: TestClient, connection_id: str
) -> None:
    _post_index(catalog_client, connection_id)
    tables = {t["name"]: t for t in _tables(catalog_client, connection_id)}
    _patch(catalog_client, "tables", tables["production"]["id"], selected=True)
    _patch(catalog_client, "tables", tables["plants"]["id"], selected=False)

    data = _post_index(catalog_client, connection_id)

    assert data == {"status": "done", "indexed_tables": 1, "indexed_columns": 2}
    assert _get_index(catalog_client, connection_id) == data


def test_failed_embedding_reports_failed_without_leaking_the_error(
    catalog_client: TestClient,
    connection_id: str,
    catalog_embeddings: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog_embeddings.error = RuntimeError(SENTINEL_ERROR)

    response = catalog_client.post(f"{CATALOG}/{connection_id}/index")

    assert response.status_code == 200
    assert response.json()["data"] == FAILED
    assert SENTINEL_ERROR not in response.text
    captured = capsys.readouterr()
    assert SENTINEL_ERROR not in captured.out + captured.err
    assert _get_index(catalog_client, connection_id) == IDLE


def test_failed_reindex_keeps_the_previous_index_in_effect(
    catalog_client: TestClient, connection_id: str, catalog_embeddings: Any
) -> None:
    first = _post_index(catalog_client, connection_id)
    catalog_embeddings.error = RuntimeError(SENTINEL_ERROR)

    assert _post_index(catalog_client, connection_id) == FAILED

    assert _get_index(catalog_client, connection_id) == first


def test_vectors_of_the_wrong_dimension_fail_the_run(
    catalog_client: TestClient, connection_id: str, catalog_embeddings: Any
) -> None:
    catalog_embeddings.dimensions = catalog_embeddings.dimensions - 3  # settings still say 8

    assert _post_index(catalog_client, connection_id) == FAILED
    assert _get_index(catalog_client, connection_id) == IDLE


def test_reimport_that_removes_an_indexed_table_drops_its_entries(
    catalog_client: TestClient, connection_id: str, warehouse_file: Path
) -> None:
    import duckdb

    _post_index(catalog_client, connection_id)
    con = duckdb.connect(str(warehouse_file))
    try:
        con.execute("DROP TABLE plants")
    finally:
        con.close()

    catalog_client.post(f"{CATALOG}/{connection_id}/import")

    assert _get_index(catalog_client, connection_id) == IDLE


@pytest.mark.parametrize("method", ["get", "post"])
def test_index_of_an_unknown_connection_returns_404(
    catalog_client: TestClient, method: str
) -> None:
    response = getattr(catalog_client, method)(f"{CATALOG}/{uuid.uuid4()}/index")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "connection_not_found"
