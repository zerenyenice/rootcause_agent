"""The ``*`` guard of SelectionExecutor: no expansion over hidden columns (Sensitive)."""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from rootcause.catalog.restricted_executor import (
    HIDDEN_COLUMNS_CODE,
    SelectedTable,
    SelectionExecutor,
)
from rootcause.connections.duckdb_executor import DuckDBExecutor, QueryRejected

pytestmark = pytest.mark.unit


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE t (id INTEGER, name VARCHAR, secret VARCHAR)")
        con.execute("INSERT INTO t VALUES (1,'a','x'),(2,'b','y')")
        con.execute("CREATE TABLE open_t (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO open_t VALUES (1,'a'),(2,'b')")
    finally:
        con.close()
    return path


@pytest.fixture
def executor(db_path: Path) -> Iterator[SelectionExecutor]:
    tables = (
        SelectedTable("main", "t", "t", ("id", "name"), hides_columns=True),
        SelectedTable("main", "open_t", "open_t", ("id", "name"), hides_columns=False),
    )
    ex = SelectionExecutor(DuckDBExecutor(db_path), tables, "duckdb")
    try:
        yield ex
    finally:
        ex.close()


def test_the_hidden_columns_code_is_stable() -> None:
    assert HIDDEN_COLUMNS_CODE == "star_over_hidden_columns"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t",
        "SELECT p.* FROM t p",
        "SELECT * EXCLUDE (name) FROM t",
        "SELECT * FROM t JOIN open_t o ON t.id = o.id",
    ],
)
def test_star_over_hidden_columns_is_rejected(executor: SelectionExecutor, sql: str) -> None:
    with pytest.raises(QueryRejected) as error:
        executor.run(sql, "probe")
    assert HIDDEN_COLUMNS_CODE in str(error.value)


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT p FROM t p", "unknown_column"),
        ("SELECT COLUMNS('.*') FROM t", "forbidden_function"),
    ],
    ids=["bare-row-reference", "columns-expression"],
)
def test_other_star_like_forms_are_rejected_before_the_star_guard(
    executor: SelectionExecutor, sql: str, code: str
) -> None:
    # The validator already refuses these (a row reference is no selected column,
    # COLUMNS() is not allow-listed), so they never reach the star guard.
    with pytest.raises(QueryRejected) as error:
        executor.run(sql, "probe")
    assert code in str(error.value)


def test_explain_applies_the_same_star_guard(executor: SelectionExecutor) -> None:
    with pytest.raises(QueryRejected) as error:
        executor.explain("SELECT * FROM t")
    assert HIDDEN_COLUMNS_CODE in str(error.value)


def test_count_star_over_a_table_with_hidden_columns_is_allowed(
    executor: SelectionExecutor,
) -> None:
    assert executor.run("SELECT count(*) AS n FROM t", "count").rows == ((2,),)


def test_explicit_selected_columns_are_allowed(executor: SelectionExecutor) -> None:
    assert executor.run("SELECT id, name FROM t ORDER BY id", "cols").rows == ((1, "a"), (2, "b"))


def test_star_over_a_fully_selected_table_is_allowed(executor: SelectionExecutor) -> None:
    result = executor.run("SELECT * FROM open_t ORDER BY id", "all")
    assert result.rows == ((1, "a"), (2, "b"))


def test_a_hidden_column_named_explicitly_is_not_a_star_rejection(
    executor: SelectionExecutor,
) -> None:
    with pytest.raises(QueryRejected) as error:
        executor.run("SELECT secret FROM t", "hidden")
    assert HIDDEN_COLUMNS_CODE not in str(error.value)
