"""Strict selection check regressions: alias shadowing, renames, scope binding (Sensitive).

Real ``DuckDBExecutor`` on tmp files. ``t(id, visible, secret)`` hides ``secret``
(value ``S3CRET``); ``u(id, label)`` is fully selected. Every statement here would
read the hidden column through a binding the database resolves differently from the
selection snapshot; each must be rejected by ``run`` and ``explain`` and the secret
must never come back. Controls prove legitimate shapes still work.
"""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from rootcause.catalog.restricted_executor import SelectedTable, SelectionExecutor
from rootcause.connections.duckdb_executor import DuckDBExecutor, QueryRejected

pytestmark = pytest.mark.unit

SECRET = "S3CRET"


def _flatten(rows: tuple[tuple[object, ...], ...]) -> list[str]:
    return [str(value) for row in rows for value in row]


@pytest.fixture
def strict_executor(tmp_path: Path) -> Iterator[SelectionExecutor]:
    path = tmp_path / "strict.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE t (id INTEGER, visible INTEGER, secret VARCHAR)")
        con.execute(f"INSERT INTO t VALUES (1, 10, '{SECRET}'), (2, 20, '{SECRET}')")
        con.execute("CREATE TABLE u (id INTEGER, label VARCHAR)")
        con.execute("INSERT INTO u VALUES (1, 'one'), (2, 'two')")
    finally:
        con.close()
    tables = (
        SelectedTable("main", "t", "t", ("id", "visible"), hides_columns=True),
        SelectedTable("main", "u", "u", ("id", "label"), hides_columns=False),
    )
    ex = SelectionExecutor(DuckDBExecutor(path), tables, "duckdb")
    try:
        yield ex
    finally:
        ex.close()


LEAKS = {
    "alias-shadow-group-by": "SELECT visible AS secret FROM t GROUP BY secret",
    "alias-shadow-having": (
        "SELECT visible AS secret FROM t GROUP BY secret HAVING max(secret) > 'a'"
    ),
    "alias-shadow-order-by-function": "SELECT visible AS secret FROM t ORDER BY lower(secret)",
    "positional-rename-hidden-to-visible": "SELECT visible FROM t AS t(id, secret, visible)",
    "alias-list-selected-names": "SELECT id FROM t AS x(id, visible)",
    "alias-list-three-names": "SELECT a FROM t AS x(a, b, c)",
    "alias-list-in-join": "SELECT x.a FROM t AS x(a, b, c) JOIN u ON u.id = x.a",
    "outer-scope-column-inside-subquery": "SELECT (SELECT x FROM t) FROM (VALUES ('a')) v(x)",
    "filter-clause": "SELECT count(*) FILTER (WHERE secret = 'S3CRET') FROM t",
    "min-by": "SELECT min_by(id, secret) FROM t",
    "max-by": "SELECT max_by(id, secret) FROM t",
    "max-by-value": "SELECT max_by(secret, id) FROM t",
    "struct-literal": "SELECT {'a': secret} FROM t",
    "asof-using": "SELECT visible FROM t ASOF JOIN u USING (id)",
    "positional-join-star": "SELECT * FROM t POSITIONAL JOIN u",
    "positional-join-star-reversed": "SELECT * FROM u POSITIONAL JOIN t",
    "three-part-name": "SELECT id FROM memory.main.t",
    "three-part-name-other-catalog": "SELECT id FROM other.main.t",
}


@pytest.mark.parametrize("sql", list(LEAKS.values()), ids=list(LEAKS))
def test_run_rejects_the_leak_and_never_returns_the_secret(
    strict_executor: SelectionExecutor, sql: str
) -> None:
    rows: tuple[tuple[object, ...], ...] = ()
    try:
        rows = strict_executor.run(sql, "probe").rows
    except QueryRejected:
        return
    assert SECRET not in _flatten(rows), "hidden column value leaked"
    pytest.fail("statement was not rejected")


@pytest.mark.parametrize("sql", list(LEAKS.values()), ids=list(LEAKS))
def test_explain_rejects_the_leak(strict_executor: SelectionExecutor, sql: str) -> None:
    with pytest.raises(QueryRejected):
        strict_executor.explain(sql)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT visible AS v FROM t ORDER BY v", ((10,), (20,))),
        ("SELECT visible FROM t ORDER BY visible", ((10,), (20,))),
        ("SELECT id, visible FROM t ORDER BY id DESC", ((2, 20), (1, 10))),
        (
            "WITH t2 AS (SELECT id FROM u) SELECT id FROM t2 ORDER BY id",
            ((1,), (2,)),
        ),
        (
            "WITH t AS (SELECT id FROM u) SELECT id FROM t ORDER BY id",
            ((1,), (2,)),
        ),
        ("SELECT {'a': id} AS s FROM t ORDER BY id", (({"a": 1},), ({"a": 2},))),
        ("SELECT max_by(id, visible) FROM t", ((2,),)),
        ("SELECT count(*) FILTER (WHERE visible > 10) FROM t", ((1,),)),
    ],
    ids=[
        "alias-order-by",
        "plain-order-by",
        "order-by-desc",
        "cte-name-not-a-table",
        "cte-shadows-restricted-table",
        "struct-selected-column",
        "max-by-selected",
        "filter-selected",
    ],
)
def test_legitimate_shapes_still_work(
    strict_executor: SelectionExecutor, sql: str, expected: tuple[tuple[object, ...], ...]
) -> None:
    result = strict_executor.run(sql, "control")
    assert result.rows == expected
    assert SECRET not in _flatten(result.rows)
