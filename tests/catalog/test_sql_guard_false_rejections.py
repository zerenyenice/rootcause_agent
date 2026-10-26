"""Executor-level regressions: legitimate analytic SQL runs; rejections explain themselves.

Real ``DuckDBExecutor`` / ``SelectionExecutor`` on a tmp file. ``t(id, grp, secret)`` hides
``secret``; ``u(id, label)`` is fully selected.
"""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from rootcause.catalog.restricted_executor import SelectedTable, SelectionExecutor
from rootcause.connections.duckdb_executor import DuckDBExecutor, QueryRejected

pytestmark = pytest.mark.unit


@pytest.fixture
def executor(tmp_path: Path) -> Iterator[SelectionExecutor]:
    path = tmp_path / "guard.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE t (id INTEGER, grp VARCHAR, secret VARCHAR)")
        con.execute("INSERT INTO t VALUES (1, 'a', 'S3CRET'), (2, 'a', 'S3CRET'), (3, 'b', 'x')")
        con.execute("CREATE TABLE u (id INTEGER, label VARCHAR)")
        con.execute("INSERT INTO u VALUES (1, 'one'), (2, 'two')")
    finally:
        con.close()
    tables = (
        SelectedTable("main", "t", "t", ("id", "grp"), hides_columns=True),
        SelectedTable("main", "u", "u", ("id", "label"), hides_columns=False),
    )
    ex = SelectionExecutor(DuckDBExecutor(path), tables, "duckdb")
    try:
        yield ex
    finally:
        ex.close()


SAFE = [
    "SELECT id * 2 AS dbl, dbl + 1 AS plus FROM u ORDER BY id",
    "SELECT label AS l, count(*) AS n FROM u WHERE l <> 'z' GROUP BY l",
    "SELECT DISTINCT ON (grp) grp, id FROM t ORDER BY grp, id DESC",
    "SELECT regr_slope(id, id), mean(id), arbitrary(grp) FROM t",
    "SELECT date_sub('day', DATE '2024-01-01', DATE '2024-01-10') FROM u",
]


@pytest.mark.parametrize("sql", SAFE)
def test_legitimate_analytic_query_runs_through_the_selection_executor(
    executor: SelectionExecutor, sql: str
) -> None:
    result = executor.run(sql, "test")

    assert result.row_count >= 1


HIDDEN = [
    "SELECT DISTINCT ON (secret) id FROM t",
    "SELECT secret AS s, s FROM t",
    "SELECT id FROM t WHERE secret = 'S3CRET'",
    "SELECT DISTINCT ON (grp) id FROM t ORDER BY secret",
    "SELECT COLUMNS('.*') FROM t",
]


@pytest.mark.parametrize("sql", HIDDEN)
def test_hidden_column_is_still_rejected_in_the_relaxed_shapes(
    executor: SelectionExecutor, sql: str
) -> None:
    with pytest.raises(QueryRejected):
        executor.run(sql, "test")


def test_unknown_column_rejection_names_the_available_columns(
    executor: SelectionExecutor,
) -> None:
    with pytest.raises(QueryRejected) as caught:
        executor.run("SELECT u.nope FROM u", "test")

    rejected = caught.value
    assert rejected.codes == ("unknown_column",)
    assert any("columns are: id, label" in reason for reason in rejected.reasons)
    assert any("describe_table" in hint for hint in rejected.hints)


def test_hidden_columns_are_not_listed_in_the_rejection(executor: SelectionExecutor) -> None:
    with pytest.raises(QueryRejected) as caught:
        executor.run("SELECT t.nope FROM t", "test")

    assert all("secret" not in reason for reason in caught.value.reasons)


def test_generator_rejection_carries_rewrite_guidance(executor: SelectionExecutor) -> None:
    with pytest.raises(QueryRejected) as caught:
        executor.run("SELECT unnest([1, 2]) AS x FROM u", "test")

    assert caught.value.codes == ("generator_function",)
    assert any("generate_series" in hint for hint in caught.value.hints)
