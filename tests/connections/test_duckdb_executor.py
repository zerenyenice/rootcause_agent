"""DuckDB warehouse executor: read-only, locked down, bounded, schema-introspecting (RED)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from rootcause.connections.duckdb_executor import (
    ColumnInfo,
    DuckDBExecutor,
    QueryRejected,
    QueryTimeout,
    TableInfo,
    UnknownTable,
    executor_from_settings,
)
from rootcause.errors import ConfigurationError
from rootcause.settings import Settings

PLANT_COUNT = 3
PRODUCTION_ROWS = 300


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE plants (plant_id VARCHAR, plant_name VARCHAR)")
    con.execute("INSERT INTO plants VALUES ('P01','Alpha'),('P02','Beta'),('P03','Gamma')")
    con.execute(
        "CREATE TABLE production (ts INTEGER, plant_id VARCHAR, units INTEGER, scrap DOUBLE)"
    )
    con.execute(
        f"INSERT INTO production SELECT i, 'P0' || (1 + i % {PLANT_COUNT}), i % 50, (i % 7) / 10.0 "
        f"FROM range({PRODUCTION_ROWS}) t(i)"
    )
    con.close()
    return path


@pytest.fixture
def executor(db_path: Path) -> Iterator[DuckDBExecutor]:
    with DuckDBExecutor(db_path) as ex:
        yield ex


def _tables(path: Path) -> dict[str, int]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        names = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        return {n: con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}  # type: ignore[index]
    finally:
        con.close()


@pytest.mark.unit
def test_run_returns_rows_columns_and_validated_sql(executor: DuckDBExecutor) -> None:
    result = executor.run("SELECT plant_id, plant_name FROM plants ORDER BY plant_id", "list")
    assert result.columns == ("plant_id", "plant_name")
    assert result.rows == (("P01", "Alpha"), ("P02", "Beta"), ("P03", "Gamma"))
    assert result.row_count == 3
    assert result.truncated is False
    assert "LIMIT" in result.sql.upper()


@pytest.mark.unit
def test_run_caps_rows_at_max_rows_and_flags_truncation(db_path: Path) -> None:
    with DuckDBExecutor(db_path, max_rows=50) as ex:
        result = ex.run("SELECT * FROM production", "scan")
    assert len(result.rows) == 50
    assert result.row_count == 50
    assert result.truncated is True


@pytest.mark.unit
def test_run_not_truncated_when_rows_equal_cap(db_path: Path) -> None:
    with DuckDBExecutor(db_path, max_rows=PRODUCTION_ROWS) as ex:
        result = ex.run("SELECT * FROM production", "scan")
    assert len(result.rows) == PRODUCTION_ROWS
    assert result.truncated is False


@pytest.mark.unit
def test_explicit_smaller_limit_is_not_truncated(executor: DuckDBExecutor) -> None:
    result = executor.run("SELECT * FROM production LIMIT 5", "peek")
    assert len(result.rows) == 5
    assert result.truncated is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM plants",
        "INSERT INTO plants VALUES ('P99','X')",
        "CREATE TABLE evil (a INT)",
        "DROP TABLE plants",
        "UPDATE plants SET plant_name = 'x'",
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('http://example.invalid/x.parquet')",
        "ATTACH 'other.duckdb' AS o",
        "INSTALL httpfs",
        "LOAD httpfs",
        "COPY plants TO '/tmp/out.csv'",
        "SET enable_external_access = true",
        "PRAGMA database_list",
        "SELECT current_setting('enable_external_access')",
        "",
        "not sql at all (",
    ],
)
def test_unsafe_or_invalid_sql_is_rejected_and_file_unchanged(db_path: Path, sql: str) -> None:
    before = _tables(db_path)
    with DuckDBExecutor(db_path) as ex, pytest.raises(QueryRejected) as info:
        ex.run(sql, "attack")
    assert info.value.codes
    assert sql.strip() == "" or sql not in str(info.value)
    assert _tables(db_path) == before


@pytest.mark.unit
def test_rejection_message_contains_no_sql_text(executor: DuckDBExecutor) -> None:
    secret = "SENTINEL_TABLE_NAME_123"
    with pytest.raises(QueryRejected) as info:
        executor.run(f"DELETE FROM {secret}", "x")
    assert secret not in str(info.value)
    assert all(isinstance(c, str) for c in info.value.codes)


@pytest.mark.unit
def test_connection_is_read_only_and_locked(executor: DuckDBExecutor) -> None:
    snapshot = executor.settings_snapshot()
    assert str(snapshot["enable_external_access"]).lower() == "false"
    assert str(snapshot["lock_configuration"]).lower() == "true"


@pytest.mark.unit
def test_underlying_connection_refuses_writes_and_external_reads(
    executor: DuckDBExecutor,
) -> None:
    con: Any = executor._connection  # noqa: SLF001 - test-only access
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM plants")
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM read_csv('/etc/passwd')")
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access = true")


@pytest.mark.unit
def test_timeout_raises_query_timeout_quickly(db_path: Path) -> None:
    with DuckDBExecutor(db_path, timeout_seconds=0.2) as ex:
        started = time.perf_counter()
        with pytest.raises(QueryTimeout):
            ex.run(
                "SELECT sum(a.x * b.y) FROM range(100000) AS a(x) CROSS JOIN range(100000) AS b(y)",
                "heavy",
            )
        assert time.perf_counter() - started < 3.0
        # The executor stays usable after a timeout.
        assert ex.run("SELECT count(*) AS n FROM plants", "after").rows == ((3,),)


@pytest.mark.unit
def test_explain_returns_non_negative_estimate(executor: DuckDBExecutor) -> None:
    estimate = executor.explain("SELECT * FROM production")
    assert estimate.estimated_rows >= 0
    assert estimate.estimated_cost >= 0


@pytest.mark.unit
@pytest.mark.parametrize("sql", ["DELETE FROM plants", "SELECT 1; SELECT 2", ""])
def test_explain_rejects_invalid_sql(executor: DuckDBExecutor, sql: str) -> None:
    with pytest.raises(QueryRejected):
        executor.explain(sql)


@pytest.mark.unit
def test_list_tables_returns_names_and_exact_row_counts(executor: DuckDBExecutor) -> None:
    tables = executor.list_tables()
    assert all(isinstance(t, TableInfo) for t in tables)
    assert {t.name: t.row_count for t in tables} == {
        "plants": PLANT_COUNT,
        "production": PRODUCTION_ROWS,
    }


@pytest.mark.unit
def test_describe_table_returns_columns_with_types(executor: DuckDBExecutor) -> None:
    columns = executor.describe_table("production")
    assert all(isinstance(c, ColumnInfo) for c in columns)
    assert [c.name for c in columns] == ["ts", "plant_id", "units", "scrap"]
    types = {c.name: c.data_type.upper() for c in columns}
    assert types["plant_id"] == "VARCHAR"
    assert types["scrap"] == "DOUBLE"


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "nope",
        "",
        "plants; DROP TABLE plants",
        'plants"',
        "plants--",
        "' OR 1=1 --",
        "information_schema.tables",
        "pläntś\U0001f600",
    ],
)
def test_describe_unknown_or_non_identifier_table_raises(
    db_path: Path, executor: DuckDBExecutor, name: str
) -> None:
    with pytest.raises(UnknownTable):
        executor.describe_table(name)
    assert _tables(db_path)["plants"] == PLANT_COUNT


@pytest.mark.unit
def test_distinct_values_returns_strings_respecting_limit(executor: DuckDBExecutor) -> None:
    assert sorted(executor.distinct_values("plants", "plant_id")) == ["P01", "P02", "P03"]
    limited = executor.distinct_values("production", "ts", limit=7)
    assert len(limited) == 7
    assert all(isinstance(v, str) for v in limited)
    assert len(executor.distinct_values("production", "ts")) == 50


@pytest.mark.unit
def test_distinct_values_default_limit_is_fifty(executor: DuckDBExecutor) -> None:
    assert len(executor.distinct_values("production", "ts")) == 50
    assert len(executor.distinct_values("production", "units")) == 50


@pytest.mark.unit
@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("nope", "plant_id"),
        ("plants", "nope"),
        ("plants", "plant_id; DROP TABLE plants"),
        ("plants; DROP TABLE plants", "plant_id"),
        ("plants", ""),
    ],
)
def test_distinct_values_unknown_table_or_column_raises(
    db_path: Path, executor: DuckDBExecutor, table: str, column: str
) -> None:
    with pytest.raises(UnknownTable):
        executor.distinct_values(table, column)
    assert _tables(db_path)["plants"] == PLANT_COUNT


@pytest.mark.unit
def test_close_is_idempotent_and_blocks_further_use(db_path: Path) -> None:
    ex = DuckDBExecutor(db_path)
    ex.close()
    ex.close()
    with pytest.raises(Exception):  # noqa: B017, PT011 - any failure beats silent success
        ex.run("SELECT 1", "after close")


@pytest.mark.unit
def test_missing_database_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011
        DuckDBExecutor(tmp_path / "absent.duckdb")
    assert not (tmp_path / "absent.duckdb").exists()


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://u:p@localhost/db",
        environment="test",
        **overrides,
    )


@pytest.mark.unit
def test_executor_from_settings_none_when_unset() -> None:
    assert executor_from_settings(_settings()) is None


@pytest.mark.unit
def test_executor_from_settings_opens_configured_path(db_path: Path) -> None:
    ex = executor_from_settings(_settings(warehouse_path=db_path))
    assert isinstance(ex, DuckDBExecutor)
    with ex:
        assert ex.run("SELECT count(*) FROM plants", "n").rows == ((PLANT_COUNT,),)


@pytest.mark.unit
def test_executor_from_settings_missing_path_raises_configuration_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError):
        executor_from_settings(_settings(warehouse_path=tmp_path / "absent.duckdb"))


@pytest.mark.unit
def test_warehouse_path_reads_prefixed_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ROOTCAUSE_WAREHOUSE_PATH", str(tmp_path / "w.duckdb"))
    assert _settings().warehouse_path == tmp_path / "w.duckdb"
    monkeypatch.delenv("ROOTCAUSE_WAREHOUSE_PATH")
    assert _settings().warehouse_path is None


@pytest.mark.integration
def test_real_warehouse_with_e1_incident(tmp_path: Path) -> None:
    from rootcause.evals.manufacturing import GeneratorConfig, generate
    from rootcause.evals.manufacturing.incidents import instantiate
    from rootcause.evals.manufacturing.writers import write_duckdb

    config = GeneratorConfig(seed=7, days=120)
    tables = generate(config, instantiate(config, archetypes=("E1",)))
    path = tmp_path / "warehouse.duckdb"
    write_duckdb(tables, path)
    with DuckDBExecutor(path) as ex:
        names = {t.name for t in ex.list_tables()}
        assert set(tables) == names
        assert ex.describe_table("plants")
        result = ex.run("SELECT count(*) AS n FROM plants", "count")
        assert result.rows == ((len(tables["plants"]),),)
