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


@pytest.fixture
def macro_executors(tmp_path: Path) -> Iterator[tuple[DuckDBExecutor, SelectionExecutor]]:
    """A file with customer macros that read unselected data; one executor of each kind."""
    path = tmp_path / "macros.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE t (a INTEGER, b VARCHAR, secret VARCHAR)")
        con.execute("INSERT INTO t VALUES (1, 'x', 'HIDDEN')")
        con.execute("CREATE TABLE other (pw VARCHAR)")
        con.execute("INSERT INTO other VALUES ('OTHERSECRET')")
        con.execute("CREATE MACRO leak() AS (SELECT string_agg(pw) FROM other)")
        con.execute("CREATE MACRO leak2(x) AS (SELECT string_agg(secret) FROM t)")
        con.execute("CREATE MACRO round(x) AS (SELECT string_agg(pw) FROM other)")
        con.execute("CREATE MACRO tleak() AS TABLE SELECT * FROM other")
    finally:
        con.close()
    inner = DuckDBExecutor(path)
    selected = (SelectedTable("main", "t", "t", ("a", "b"), hides_columns=True),)
    selection = SelectionExecutor(inner, selected, "duckdb")
    try:
        yield inner, selection
    finally:
        selection.close()


MACRO_SQL = [
    "SELECT leak() FROM t",
    "SELECT leak2(a) FROM t",
    "SELECT main.leak() FROM t",
    "SELECT round(a) FROM t",
    "SELECT * FROM tleak()",
]


@pytest.mark.parametrize("sql", MACRO_SQL)
def test_user_defined_macro_is_rejected_by_both_executors(
    macro_executors: tuple[DuckDBExecutor, SelectionExecutor], sql: str
) -> None:
    for executor in macro_executors:
        with pytest.raises(QueryRejected) as caught:
            executor.run(sql, "test")
        assert "forbidden_function" in caught.value.codes


def test_unknown_function_is_rejected_and_builtins_still_work(
    macro_executors: tuple[DuckDBExecutor, SelectionExecutor],
) -> None:
    inner, selection = macro_executors

    with pytest.raises(QueryRejected):
        selection.run("SELECT totally_unknown_function(a) FROM t", "test")
    assert selection.run("SELECT date_sub('day', DATE '2024-01-01', DATE '2024-01-03')", "t").rows
    assert inner.function_catalog.user >= {"leak", "leak2", "round", "tleak"}
    assert "date_sub" in inner.function_catalog.builtin
    assert "leak" not in inner.function_catalog.builtin


@pytest.mark.parametrize(
    "sql", ["SELECT a AS secret, secret FROM t", "SELECT 1 FROM t WHERE x = 2"]
)
def test_selection_rejection_from_the_inner_executor_carries_codes_only(
    macro_executors: tuple[DuckDBExecutor, SelectionExecutor], sql: str
) -> None:
    _, selection = macro_executors

    with pytest.raises(QueryRejected) as caught:
        selection.run(sql, "test")

    assert "secret" not in " ".join(caught.value.reasons)


def test_inner_executor_reasons_are_dropped(
    macro_executors: tuple[DuckDBExecutor, SelectionExecutor], monkeypatch: pytest.MonkeyPatch
) -> None:
    inner, selection = macro_executors

    def reject(sql: str, purpose: str) -> None:
        raise QueryRejected(["unknown_column"], ["Unknown column 'x'; columns are: a, secret."])

    monkeypatch.setattr(inner, "run", reject)

    with pytest.raises(QueryRejected) as caught:
        selection.run("SELECT a FROM t", "test")

    assert caught.value.codes == ("unknown_column",)
    assert caught.value.reasons == ()
