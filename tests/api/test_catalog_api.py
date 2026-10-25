"""``/api/v1/catalog``: import, list and edit the metadata catalog of a connection (RED).

Routes use in-memory connection and catalog repositories through dependency
overrides; DuckDB connections are introspected for real (a small file per test), so
nothing is mocked below the HTTP layer. Contract decisions pinned here:

- Import upserts by (schema, table) and (table, column): ids, ``selected`` and
  ``description`` survive a re-import; tables and columns gone from the source are
  deleted. A newly discovered table is unselected and its columns are selected (the
  table is the gate); a column that appears on an already-known table is unselected,
  so nothing new becomes visible to the agent without a human action.
- Import responds with ``{"tables": n, "columns": m}``: the totals now in the catalog.
- ``GET .../tables`` sorts tables by (schema, name) and columns in declaration order.
- PATCH bodies: ``selected`` (strict bool, never null) and ``description`` (at most
  2000 chars, no NUL; ``null`` or blank clears it, stored as ``null``); unknown fields
  are rejected. A table PATCH returns the table (with columns), a column PATCH the column.
"""

import uuid
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

CONNECTIONS = "/api/v1/connections"
CATALOG = "/api/v1/catalog"
TABLE_FIELDS = {"id", "schema", "name", "selected", "description", "row_estimate", "columns"}
COLUMN_FIELDS = {"id", "name", "type", "nullable", "selected", "description"}
MAX_DESCRIPTION = 2000


# --- helpers -----------------------------------------------------------------


def _duckdb(client: TestClient, path: Path) -> str:
    response = client.post(CONNECTIONS, json={"name": "demo", "kind": "duckdb", "path": str(path)})
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def _import(client: TestClient, connection_id: str) -> dict[str, Any]:
    response = client.post(f"{CATALOG}/{connection_id}/import")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    data: dict[str, Any] = body["data"]
    return data


def _tables(client: TestClient, connection_id: str) -> list[dict[str, Any]]:
    response = client.get(f"{CATALOG}/{connection_id}/tables")
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()["data"]
    return data


def _by_name(tables: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [table for table in tables if table["name"] == name]
    assert len(matches) == 1, [t["name"] for t in tables]
    return matches[0]


def _column(table: dict[str, Any], name: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = [c for c in table["columns"] if c["name"] == name]
    assert len(matches) == 1, [c["name"] for c in table["columns"]]
    return matches[0]


def _alter(path: Path, *statements: str) -> None:
    con = duckdb.connect(str(path))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()


@pytest.fixture
def imported(catalog_client: TestClient, warehouse_file: Path) -> str:
    """A DuckDB connection over ``warehouse_file`` whose catalog has been imported once."""
    connection_id = _duckdb(catalog_client, warehouse_file)
    _import(catalog_client, connection_id)
    return connection_id


# --- import --------------------------------------------------------------------


def test_import_returns_table_and_column_counts(
    catalog_client: TestClient, warehouse_file: Path
) -> None:
    connection_id = _duckdb(catalog_client, warehouse_file)

    data = _import(catalog_client, connection_id)

    assert data == {"tables": 2, "columns": 4}


def test_import_of_an_unknown_connection_returns_404(catalog_client: TestClient) -> None:
    response = catalog_client.post(f"{CATALOG}/{uuid.uuid4()}/import")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "connection_not_found"


def test_import_with_a_malformed_connection_id_returns_422(catalog_client: TestClient) -> None:
    response = catalog_client.post(f"{CATALOG}/not-a-uuid/import")

    assert response.status_code == 422


def test_import_of_an_unreachable_source_returns_503_and_keeps_the_catalog(
    catalog_client: TestClient, imported: str, warehouse_file: Path
) -> None:
    warehouse_file.unlink()

    response = catalog_client.post(f"{CATALOG}/{imported}/import")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "connection_unavailable"
    assert str(warehouse_file) not in response.text
    assert [t["name"] for t in _tables(catalog_client, imported)] == ["plants", "production"]


def test_import_does_not_modify_the_warehouse_file(
    catalog_client: TestClient, warehouse_file: Path
) -> None:
    connection_id = _duckdb(catalog_client, warehouse_file)
    before = warehouse_file.read_bytes()

    _import(catalog_client, connection_id)

    assert warehouse_file.read_bytes() == before


# --- list ----------------------------------------------------------------------


def test_list_returns_the_contract_shape_with_defaults(
    catalog_client: TestClient, imported: str
) -> None:
    response = catalog_client.get(f"{CATALOG}/{imported}/tables")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["meta"]["total"] == 2
    tables = body["data"]
    assert [(t["schema"], t["name"]) for t in tables] == [
        ("main", "plants"),
        ("main", "production"),
    ]
    for table in tables:
        assert set(table) == TABLE_FIELDS
        uuid.UUID(table["id"])
        assert table["selected"] is False
        assert table["description"] is None
        for column in table["columns"]:
            assert set(column) == COLUMN_FIELDS
            uuid.UUID(column["id"])
            assert column["selected"] is True
            assert column["description"] is None
    plants, production = tables
    assert plants["row_estimate"] == 3
    assert production["row_estimate"] == 5
    assert [(c["name"], c["type"], c["nullable"]) for c in plants["columns"]] == [
        ("plant_id", "VARCHAR", False),
        ("plant_name", "VARCHAR", True),
    ]
    assert [(c["name"], c["type"], c["nullable"]) for c in production["columns"]] == [
        ("ts", "INTEGER", True),
        ("units", "INTEGER", False),
    ]


def test_list_ids_are_unique_across_tables_and_columns(
    catalog_client: TestClient, imported: str
) -> None:
    tables = _tables(catalog_client, imported)

    ids = [t["id"] for t in tables] + [c["id"] for t in tables for c in t["columns"]]

    assert len(ids) == len(set(ids)) == 6


def test_list_sorts_tables_by_name_regardless_of_creation_order(
    catalog_client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "unordered.duckdb"
    _alter(
        path,
        "CREATE TABLE zeta (z INTEGER)",
        "CREATE TABLE alpha (b INTEGER, a INTEGER)",
        "CREATE TABLE mid (m INTEGER)",
    )
    connection_id = _duckdb(catalog_client, path)
    _import(catalog_client, connection_id)

    tables = _tables(catalog_client, connection_id)

    assert [t["name"] for t in tables] == ["alpha", "mid", "zeta"]
    assert [c["name"] for c in tables[0]["columns"]] == ["b", "a"]  # declaration order


def test_list_before_any_import_is_empty(catalog_client: TestClient, warehouse_file: Path) -> None:
    connection_id = _duckdb(catalog_client, warehouse_file)

    response = catalog_client.get(f"{CATALOG}/{connection_id}/tables")

    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["meta"]["total"] == 0


def test_list_of_an_unknown_connection_returns_404(catalog_client: TestClient) -> None:
    response = catalog_client.get(f"{CATALOG}/{uuid.uuid4()}/tables")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "connection_not_found"


def test_catalogs_are_scoped_to_their_connection(
    catalog_client: TestClient, warehouse_file: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other.duckdb"
    _alter(other, "CREATE TABLE sensors (sensor_id VARCHAR)")
    first = _duckdb(catalog_client, warehouse_file)
    second = _duckdb(catalog_client, other)
    _import(catalog_client, first)
    _import(catalog_client, second)

    assert [t["name"] for t in _tables(catalog_client, first)] == ["plants", "production"]
    assert [t["name"] for t in _tables(catalog_client, second)] == ["sensors"]


# --- re-import -------------------------------------------------------------------


def test_reimport_keeps_ids_selection_and_descriptions(
    catalog_client: TestClient, imported: str
) -> None:
    before = _tables(catalog_client, imported)
    plants = _by_name(before, "plants")
    plant_name = _column(plants, "plant_name")
    catalog_client.patch(
        f"{CATALOG}/tables/{plants['id']}", json={"selected": True, "description": "Sites."}
    )
    catalog_client.patch(
        f"{CATALOG}/columns/{plant_name['id']}",
        json={"selected": False, "description": "Display name."},
    )

    data = _import(catalog_client, imported)

    assert data == {"tables": 2, "columns": 4}
    after = _tables(catalog_client, imported)
    plants_after = _by_name(after, "plants")
    assert plants_after["id"] == plants["id"]
    assert plants_after["selected"] is True
    assert plants_after["description"] == "Sites."
    name_after = _column(plants_after, "plant_name")
    assert name_after["id"] == plant_name["id"]
    assert name_after["selected"] is False
    assert name_after["description"] == "Display name."
    assert [t["id"] for t in after] == [t["id"] for t in before]


def test_reimport_adds_a_new_table_unselected_with_its_columns_selected(
    catalog_client: TestClient, imported: str, warehouse_file: Path
) -> None:
    _alter(warehouse_file, "CREATE TABLE machines (machine_id VARCHAR NOT NULL, kind VARCHAR)")

    data = _import(catalog_client, imported)

    assert data == {"tables": 3, "columns": 6}
    machines = _by_name(_tables(catalog_client, imported), "machines")
    assert machines["selected"] is False
    assert machines["description"] is None
    assert [(c["name"], c["selected"]) for c in machines["columns"]] == [
        ("machine_id", True),
        ("kind", True),
    ]


def test_reimport_adds_a_new_column_of_a_known_table_unselected(
    catalog_client: TestClient, imported: str, warehouse_file: Path
) -> None:
    plants = _by_name(_tables(catalog_client, imported), "plants")
    catalog_client.patch(f"{CATALOG}/tables/{plants['id']}", json={"selected": True})
    _alter(warehouse_file, "ALTER TABLE plants ADD COLUMN manager_email VARCHAR")

    _import(catalog_client, imported)

    plants_after = _by_name(_tables(catalog_client, imported), "plants")
    assert [(c["name"], c["selected"]) for c in plants_after["columns"]] == [
        ("plant_id", True),
        ("plant_name", True),
        ("manager_email", False),
    ]


def test_reimport_removes_tables_and_columns_gone_from_the_source(
    catalog_client: TestClient, imported: str, warehouse_file: Path
) -> None:
    production = _by_name(_tables(catalog_client, imported), "production")
    _alter(warehouse_file, "DROP TABLE production", "ALTER TABLE plants DROP COLUMN plant_name")

    data = _import(catalog_client, imported)

    assert data == {"tables": 1, "columns": 1}
    tables = _tables(catalog_client, imported)
    assert [t["name"] for t in tables] == ["plants"]
    assert [c["name"] for c in tables[0]["columns"]] == ["plant_id"]
    gone = catalog_client.patch(f"{CATALOG}/tables/{production['id']}", json={"selected": True})
    assert gone.status_code == 404


def test_reimport_refreshes_row_estimates_types_and_nullability(
    catalog_client: TestClient, imported: str, warehouse_file: Path
) -> None:
    _alter(
        warehouse_file,
        "INSERT INTO production SELECT i, i FROM range(5, 12) t(i)",
        "ALTER TABLE production ALTER COLUMN ts TYPE BIGINT",
        "ALTER TABLE plants ALTER COLUMN plant_name SET NOT NULL",
    )

    _import(catalog_client, imported)

    tables = _tables(catalog_client, imported)
    production = _by_name(tables, "production")
    assert production["row_estimate"] == 12
    assert _column(production, "ts")["type"] == "BIGINT"
    assert _column(_by_name(tables, "plants"), "plant_name")["nullable"] is False


def test_reimport_without_source_changes_is_idempotent(
    catalog_client: TestClient, imported: str
) -> None:
    before = _tables(catalog_client, imported)

    assert _import(catalog_client, imported) == {"tables": 2, "columns": 4}
    assert _tables(catalog_client, imported) == before


# --- PATCH tables --------------------------------------------------------------


def _first_table(client: TestClient, connection_id: str) -> dict[str, Any]:
    return _tables(client, connection_id)[0]


def test_patch_table_sets_selected_and_description_and_returns_the_table(
    catalog_client: TestClient, imported: str
) -> None:
    table = _first_table(catalog_client, imported)

    response = catalog_client.patch(
        f"{CATALOG}/tables/{table['id']}",
        json={"selected": True, "description": "Manufacturing sites."},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == TABLE_FIELDS
    assert data["id"] == table["id"]
    assert data["selected"] is True
    assert data["description"] == "Manufacturing sites."
    assert data["columns"] == table["columns"]
    listed = _first_table(catalog_client, imported)
    assert listed["selected"] is True
    assert listed["description"] == "Manufacturing sites."


def test_patch_table_changes_only_the_fields_sent(
    catalog_client: TestClient, imported: str
) -> None:
    table = _first_table(catalog_client, imported)
    path = f"{CATALOG}/tables/{table['id']}"
    catalog_client.patch(path, json={"selected": True, "description": "Sites."})

    catalog_client.patch(path, json={"selected": False})
    after_selected = _first_table(catalog_client, imported)
    catalog_client.patch(path, json={"description": "Plants."})
    after_description = _first_table(catalog_client, imported)

    assert (after_selected["selected"], after_selected["description"]) == (False, "Sites.")
    assert (after_description["selected"], after_description["description"]) == (False, "Plants.")


def test_patch_with_an_empty_body_changes_nothing(
    catalog_client: TestClient, imported: str
) -> None:
    table = _first_table(catalog_client, imported)

    response = catalog_client.patch(f"{CATALOG}/tables/{table['id']}", json={})

    assert response.status_code == 200
    assert response.json()["data"] == table


@pytest.mark.parametrize("cleared", [None, "", "   "])
def test_patch_null_or_blank_description_clears_it(
    catalog_client: TestClient, imported: str, cleared: str | None
) -> None:
    table = _first_table(catalog_client, imported)
    path = f"{CATALOG}/tables/{table['id']}"
    catalog_client.patch(path, json={"description": "Sites."})

    response = catalog_client.patch(path, json={"description": cleared})

    assert response.status_code == 200
    assert response.json()["data"]["description"] is None
    assert _first_table(catalog_client, imported)["description"] is None


def test_patch_accepts_a_description_of_exactly_2000_chars(
    catalog_client: TestClient, imported: str
) -> None:
    table = _first_table(catalog_client, imported)
    text = "d" * MAX_DESCRIPTION

    response = catalog_client.patch(f"{CATALOG}/tables/{table['id']}", json={"description": text})

    assert response.status_code == 200
    assert response.json()["data"]["description"] == text


@pytest.mark.parametrize(
    "body",
    [
        {"description": "d" * (MAX_DESCRIPTION + 1)},
        {"description": "nul \x00 byte"},
        {"description": 42},
        {"selected": "maybe"},
        {"selected": None},
        {"selected": [True]},
        {"name": "renamed"},
        {"id": str(uuid.uuid4())},
        {"schema": "public"},
        {"row_estimate": 1},
        {"selected": True, "unexpected": 1},
    ],
    ids=[
        "description-too-long",
        "description-nul",
        "description-not-string",
        "selected-not-bool",
        "selected-null",
        "selected-list",
        "name-not-editable",
        "id-not-editable",
        "schema-not-editable",
        "row-estimate-not-editable",
        "extra-field",
    ],
)
def test_patch_table_rejects_invalid_bodies_with_422_and_changes_nothing(
    catalog_client: TestClient, imported: str, body: dict[str, Any]
) -> None:
    table = _first_table(catalog_client, imported)

    response = catalog_client.patch(f"{CATALOG}/tables/{table['id']}", json=body)

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert _first_table(catalog_client, imported) == table


@pytest.mark.parametrize("body", [[{"selected": True}], "selected", 1])
def test_patch_body_must_be_a_json_object(
    catalog_client: TestClient, imported: str, body: Any
) -> None:
    table = _first_table(catalog_client, imported)

    response = catalog_client.patch(f"{CATALOG}/tables/{table['id']}", json=body)

    assert response.status_code == 422


def test_patch_description_round_trips_unicode_and_sql_metacharacters(
    catalog_client: TestClient, imported: str
) -> None:
    table = _first_table(catalog_client, imported)
    text = 'Üretim hattı ☃ 🏭 — \'; DROP TABLE catalog.tables; -- "quoted" %s {x} \\n'

    catalog_client.patch(f"{CATALOG}/tables/{table['id']}", json={"description": text})

    assert _first_table(catalog_client, imported)["description"] == text


def test_patch_of_an_unknown_table_returns_404(catalog_client: TestClient) -> None:
    response = catalog_client.patch(f"{CATALOG}/tables/{uuid.uuid4()}", json={"selected": True})

    assert response.status_code == 404
    assert response.json()["success"] is False


def test_patch_table_with_a_malformed_id_returns_422(catalog_client: TestClient) -> None:
    response = catalog_client.patch(f"{CATALOG}/tables/not-a-uuid", json={"selected": True})

    assert response.status_code == 422


def test_patch_table_does_not_accept_a_column_id(catalog_client: TestClient, imported: str) -> None:
    column = _first_table(catalog_client, imported)["columns"][0]

    response = catalog_client.patch(f"{CATALOG}/tables/{column['id']}", json={"selected": True})

    assert response.status_code == 404


# --- PATCH columns -------------------------------------------------------------


def test_patch_column_sets_selected_and_description_and_returns_the_column(
    catalog_client: TestClient, imported: str
) -> None:
    table = _first_table(catalog_client, imported)
    column = table["columns"][1]

    response = catalog_client.patch(
        f"{CATALOG}/columns/{column['id']}",
        json={"selected": False, "description": "Human-readable plant name."},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == COLUMN_FIELDS
    assert data["id"] == column["id"]
    assert data["name"] == column["name"]
    assert data["selected"] is False
    assert data["description"] == "Human-readable plant name."
    listed = _column(_first_table(catalog_client, imported), column["name"])
    assert (listed["selected"], listed["description"]) == (False, "Human-readable plant name.")
    sibling = _column(_first_table(catalog_client, imported), table["columns"][0]["name"])
    assert sibling == table["columns"][0]


def test_patch_column_null_description_clears_it(catalog_client: TestClient, imported: str) -> None:
    column = _first_table(catalog_client, imported)["columns"][0]
    path = f"{CATALOG}/columns/{column['id']}"
    catalog_client.patch(path, json={"description": "Plant identifier."})

    response = catalog_client.patch(path, json={"description": None})

    assert response.status_code == 200
    assert response.json()["data"]["description"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"description": "d" * (MAX_DESCRIPTION + 1)},
        {"selected": None},
        {"selected": "maybe"},
        {"type": "BIGINT"},
        {"nullable": False},
        {"name": "x"},
    ],
    ids=["too-long", "selected-null", "selected-not-bool", "type", "nullable", "name"],
)
def test_patch_column_rejects_invalid_bodies_with_422(
    catalog_client: TestClient, imported: str, body: dict[str, Any]
) -> None:
    table = _first_table(catalog_client, imported)
    column = table["columns"][0]

    response = catalog_client.patch(f"{CATALOG}/columns/{column['id']}", json=body)

    assert response.status_code == 422
    assert _first_table(catalog_client, imported) == table


def test_patch_of_an_unknown_column_returns_404(catalog_client: TestClient) -> None:
    response = catalog_client.patch(f"{CATALOG}/columns/{uuid.uuid4()}", json={"selected": True})

    assert response.status_code == 404
    assert response.json()["success"] is False


def test_patch_column_does_not_accept_a_table_id(catalog_client: TestClient, imported: str) -> None:
    table = _first_table(catalog_client, imported)

    response = catalog_client.patch(f"{CATALOG}/columns/{table['id']}", json={"selected": True})

    assert response.status_code == 404
