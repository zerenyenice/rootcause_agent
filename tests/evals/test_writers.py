"""Writers: CSV, DuckDB, Postgres SQL dump and data dictionary."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

# pandas is added to the rootcause package dependencies by the implementation task;
# tolerate its absence at import time so RED fails on the missing module, not collection.
try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover
    pd = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def small_tables() -> dict[str, pd.DataFrame]:
    from rootcause.evals.manufacturing import GeneratorConfig, generate

    return generate(GeneratorConfig(seed=3, days=3))


@pytest.mark.unit
def test_write_csv_creates_one_file_per_table_with_matching_rows(
    small_tables: dict[str, pd.DataFrame], tmp_path: Path
) -> None:
    from rootcause.evals.manufacturing.writers import write_csv

    write_csv(small_tables, tmp_path)

    for name, frame in small_tables.items():
        path = tmp_path / f"{name}.csv"
        assert path.is_file()
        assert len(pd.read_csv(path)) == len(frame)


@pytest.mark.unit
def test_write_csv_creates_missing_output_directory(
    small_tables: dict[str, pd.DataFrame], tmp_path: Path
) -> None:
    from rootcause.evals.manufacturing.writers import write_csv

    target = tmp_path / "nested" / "csv"

    write_csv(small_tables, target)

    assert (target / "plants.csv").is_file()


@pytest.mark.unit
def test_write_duckdb_row_counts_match(
    small_tables: dict[str, pd.DataFrame], tmp_path: Path
) -> None:
    duckdb = pytest.importorskip("duckdb")
    from rootcause.evals.manufacturing.writers import write_duckdb

    path = tmp_path / "warehouse.duckdb"
    write_duckdb(small_tables, path)

    connection = duckdb.connect(str(path))
    try:
        for name, frame in small_tables.items():
            count = connection.execute(f"select count(*) from {name}").fetchone()[0]
            assert count == len(frame)
    finally:
        connection.close()


@pytest.mark.unit
def test_write_duckdb_overwrites_existing_file(
    small_tables: dict[str, pd.DataFrame], tmp_path: Path
) -> None:
    duckdb = pytest.importorskip("duckdb")
    from rootcause.evals.manufacturing.writers import write_duckdb

    path = tmp_path / "warehouse.duckdb"
    write_duckdb(small_tables, path)
    write_duckdb(small_tables, path)

    connection = duckdb.connect(str(path))
    try:
        count = connection.execute("select count(*) from production_runs").fetchone()[0]
    finally:
        connection.close()
    assert count == len(small_tables["production_runs"])


@pytest.mark.unit
def test_write_postgres_sql_has_create_and_copy_per_table(
    small_tables: dict[str, pd.DataFrame], tmp_path: Path
) -> None:
    from rootcause.evals.manufacturing.writers import write_postgres_sql

    path = tmp_path / "warehouse.sql"
    write_postgres_sql(small_tables, path)
    text = path.read_text(encoding="utf-8")

    assert "CREATE TABLE production_runs" in text
    assert "COPY production_runs" in text
    for name in small_tables:
        assert f"CREATE TABLE {name}" in text
        assert text.count(f"COPY {name} ") + text.count(f"COPY {name}(") >= 1
    assert text.count("COPY ") == len(small_tables)


@pytest.mark.unit
def test_write_data_dictionary_lists_every_table_and_column(
    small_tables: dict[str, pd.DataFrame], tmp_path: Path
) -> None:
    from rootcause.evals.manufacturing.writers import write_data_dictionary

    write_data_dictionary(tmp_path)
    text = (tmp_path / "DATA_DICTIONARY.md").read_text(encoding="utf-8")

    for name, frame in small_tables.items():
        assert f"## {name}" in text
        section = text.split(f"## {name}", 1)[1].split("\n## ", 1)[0]
        for column in frame.columns:
            assert f"`{column}`" in section, f"{name}.{column} undocumented"
