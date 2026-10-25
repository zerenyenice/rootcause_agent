"""``ColumnInfo.nullable`` from the DuckDB executor, needed by connection introspection (RED)."""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from rootcause.connections.duckdb_executor import ColumnInfo, DuckDBExecutor

pytestmark = pytest.mark.unit


@pytest.fixture
def executor(tmp_path: Path) -> Iterator[DuckDBExecutor]:
    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR NOT NULL, plant_name VARCHAR)")
    con.close()
    with DuckDBExecutor(path) as ex:
        yield ex


def test_describe_table_reports_nullability(executor: DuckDBExecutor) -> None:
    columns = executor.describe_table("plants")

    assert [(c.name, c.data_type, c.nullable) for c in columns] == [
        ("plant_id", "VARCHAR", False),
        ("plant_name", "VARCHAR", True),
    ]


def test_column_info_keeps_two_argument_construction_compatible() -> None:
    column = ColumnInfo("x", "INTEGER")

    assert column.nullable is True
