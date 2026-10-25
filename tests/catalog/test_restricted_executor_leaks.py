"""SelectionExecutor leak regressions: opaque sources and schema shadowing (RED, Sensitive).

Contract encoded here:

1. A hidden (unselected) column is never readable, even when the query block also
   contains a source whose columns the selection snapshot cannot enumerate (a
   ``LATERAL`` subquery, a ``VALUES`` list, a derived table joined by
   ``NATURAL``/``USING``, a table function). Such a statement is rejected
   (``QueryRejected``) by both ``run`` and ``explain``. An unqualified name is
   never resolved by guessing that it belongs to the opaque source. An opaque
   source next to a restricted table stays allowed when every column the query
   names is a selected column. (Table functions are refused outright by the
   shared validator, ``generator_function``; those cases are regression guards.)
2. An unqualified table name is accepted only when it is unique across ALL
   schemas of the source. When the same name exists in another schema, selected
   or not, the agent must qualify it, so the database can never resolve a bare
   name to an unselected table. A qualified selected table works; a qualified
   unselected one is rejected; a bare name that is unique and selected works.
"""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from rootcause.catalog.restricted_executor import SelectedTable, SelectionExecutor
from rootcause.connections.duckdb_executor import DuckDBExecutor, QueryRejected

pytestmark = pytest.mark.unit

SECRET = "S3CRET"
HIDDEN_MAIN = "HIDDEN_MAIN"
VISIBLE_SALES = "VISIBLE"


def _flatten(rows: tuple[tuple[object, ...], ...]) -> list[str]:
    return [str(value) for row in rows for value in row]


# --- CRITICAL 1: hidden columns behind an opaque source ----------------------------


@pytest.fixture
def opaque_db(tmp_path: Path) -> Path:
    path = tmp_path / "opaque.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE t (id INTEGER, visible INTEGER, secret VARCHAR)")
        con.execute(f"INSERT INTO t VALUES (1, 10, '{SECRET}'), (2, 20, '{SECRET}')")
        con.execute("CREATE TABLE u (id INTEGER, label VARCHAR)")
        con.execute("INSERT INTO u VALUES (1, 'one'), (2, 'two')")
    finally:
        con.close()
    return path


@pytest.fixture
def opaque_executor(opaque_db: Path) -> Iterator[SelectionExecutor]:
    tables = (
        SelectedTable("main", "t", "t", ("id", "visible"), hides_columns=True),
        SelectedTable("main", "u", "u", ("id", "label"), hides_columns=False),
    )
    ex = SelectionExecutor(DuckDBExecutor(opaque_db), tables, "duckdb")
    try:
        yield ex
    finally:
        ex.close()


HIDDEN_BEHIND_OPAQUE_SOURCE = [
    "SELECT secret FROM t, generate_series(1,1)",
    "SELECT secret FROM t, unnest([1]) AS u(x)",
    "SELECT secret FROM t CROSS JOIN range(1)",
    "SELECT (SELECT secret FROM generate_series(1,1)) FROM t",
    "SELECT s.x FROM t, LATERAL (SELECT secret AS x) s",
    "SELECT id FROM t WHERE EXISTS (SELECT 1 FROM generate_series(1,1) WHERE secret LIKE 'S%')",
    f"SELECT id FROM t NATURAL JOIN (SELECT '{SECRET}' AS secret) d",
    f"SELECT id FROM t JOIN (SELECT '{SECRET}' AS secret) d USING (secret)",
    "SELECT secret FROM t, (VALUES (1)) v(x)",
    "SELECT secret FROM (VALUES (1)) v(x), t",
    "SELECT secret FROM t JOIN (VALUES (1)) v(x) ON true",
    "SELECT (SELECT secret FROM (VALUES (1)) v(x)) FROM t",
    "SELECT id FROM t WHERE EXISTS (SELECT 1 FROM (VALUES (1)) v(x) WHERE secret LIKE 'S%')",
]
OPAQUE_IDS = [
    "comma-generate_series",
    "comma-unnest",
    "cross-join-range",
    "scalar-subquery-over-generate_series",
    "lateral-subquery",
    "exists-filter-over-generate_series",
    "natural-join-derived-table",
    "using-join-derived-table",
    "comma-values",
    "values-first",
    "join-values-on-true",
    "scalar-subquery-over-values",
    "exists-filter-over-values",
]


@pytest.mark.parametrize("sql", HIDDEN_BEHIND_OPAQUE_SOURCE, ids=OPAQUE_IDS)
def test_run_rejects_hidden_column_behind_an_opaque_source(
    opaque_executor: SelectionExecutor, sql: str
) -> None:
    rows: tuple[tuple[object, ...], ...] = ()
    try:
        rows = opaque_executor.run(sql, "probe").rows
    except QueryRejected:
        return
    assert SECRET not in _flatten(rows), "hidden column value leaked"
    pytest.fail("statement reading a hidden column was not rejected")


@pytest.mark.parametrize("sql", HIDDEN_BEHIND_OPAQUE_SOURCE, ids=OPAQUE_IDS)
def test_explain_rejects_hidden_column_behind_an_opaque_source(
    opaque_executor: SelectionExecutor, sql: str
) -> None:
    with pytest.raises(QueryRejected):
        opaque_executor.explain(sql)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT id, visible FROM t ORDER BY id", ((1, 10), (2, 20))),
        (
            "SELECT visible FROM t, (VALUES (1), (2)) v(x) ORDER BY visible",
            ((10,), (10,), (20,), (20,)),
        ),
        ("SELECT count(*) AS n FROM t", ((2,),)),
        (
            "WITH c AS (SELECT id, visible FROM t) SELECT visible FROM c ORDER BY visible",
            ((10,), (20,)),
        ),
        (
            "SELECT v FROM (SELECT visible AS v FROM t) s ORDER BY v",
            ((10,), (20,)),
        ),
        (
            "SELECT t.visible, u.label FROM t JOIN u ON t.id = u.id ORDER BY t.id",
            ((10, "one"), (20, "two")),
        ),
    ],
    ids=["plain", "values-source-selected-only", "count-star", "cte", "subquery", "join"],
)
def test_selected_columns_still_work(
    opaque_executor: SelectionExecutor, sql: str, expected: tuple[tuple[object, ...], ...]
) -> None:
    result = opaque_executor.run(sql, "control")
    assert result.rows == expected
    assert SECRET not in _flatten(result.rows)


# --- CRITICAL 2: an unqualified name shared by tables in two schemas ---------------
#
# DuckDBExecutor resolves bare names in ``current_schema()`` (``main``), so a bare
# ``orders`` always reads ``main.orders``. The selection snapshot must not resolve
# it to a same-named selected table in another schema.


def _shadow_db(path: Path, *, main_note: str, sales_note: str) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE main.orders (id INTEGER, note VARCHAR)")
        con.execute(f"INSERT INTO main.orders VALUES (1, '{main_note}')")
        con.execute("CREATE TABLE main.refunds (id INTEGER, amount INTEGER)")
        con.execute("INSERT INTO main.refunds VALUES (3, 30)")
        con.execute("CREATE SCHEMA sales")
        con.execute("CREATE TABLE sales.orders (id INTEGER, note VARCHAR)")
        con.execute(f"INSERT INTO sales.orders VALUES (2, '{sales_note}')")
    finally:
        con.close()
    return path


@pytest.fixture
def sales_selected(tmp_path: Path) -> Iterator[SelectionExecutor]:
    """``sales.orders`` selected; ``main.orders`` (what a bare name reads) unselected."""
    path = _shadow_db(tmp_path / "s.duckdb", main_note=HIDDEN_MAIN, sales_note=VISIBLE_SALES)
    tables = (
        SelectedTable("sales", "orders", "sales.orders", ("id", "note"), hides_columns=False),
    )
    ex = SelectionExecutor(DuckDBExecutor(path), tables, "duckdb")
    try:
        yield ex
    finally:
        ex.close()


@pytest.fixture
def main_selected(tmp_path: Path) -> Iterator[SelectionExecutor]:
    """``main.orders`` and ``main.refunds`` selected; ``sales.orders`` unselected."""
    path = _shadow_db(tmp_path / "m.duckdb", main_note=VISIBLE_SALES, sales_note=HIDDEN_MAIN)
    tables = (
        SelectedTable("main", "orders", "orders", ("id", "note"), hides_columns=False),
        SelectedTable("main", "refunds", "refunds", ("id", "amount"), hides_columns=False),
    )
    ex = SelectionExecutor(DuckDBExecutor(path), tables, "duckdb")
    try:
        yield ex
    finally:
        ex.close()


def test_run_rejects_a_bare_name_the_database_resolves_to_an_unselected_table(
    sales_selected: SelectionExecutor,
) -> None:
    rows: tuple[tuple[object, ...], ...] = ()
    try:
        rows = sales_selected.run("SELECT note FROM orders", "probe").rows
    except QueryRejected:
        return
    assert HIDDEN_MAIN not in _flatten(rows), "unselected main.orders was read"
    pytest.fail("ambiguous unqualified table name was not rejected")


def test_explain_rejects_a_bare_name_the_database_resolves_to_an_unselected_table(
    sales_selected: SelectionExecutor,
) -> None:
    with pytest.raises(QueryRejected):
        sales_selected.explain("SELECT note FROM orders")


@pytest.mark.parametrize(
    "sql",
    ["SELECT note FROM main.orders", "SELECT o.note FROM orders o JOIN sales.orders s ON true"],
    ids=["qualified-unselected", "bare-joined-with-qualified"],
)
def test_unselected_main_orders_is_never_read(sales_selected: SelectionExecutor, sql: str) -> None:
    with pytest.raises(QueryRejected):
        sales_selected.run(sql, "probe")


def test_a_bare_name_present_in_two_schemas_must_be_qualified(
    main_selected: SelectionExecutor,
) -> None:
    # Here the bare name would read the selected main.orders, but the name is not
    # unique across the source's schemas, so the contract still demands a qualifier.
    with pytest.raises(QueryRejected):
        main_selected.run("SELECT note FROM orders", "probe")


def test_the_qualified_selected_table_works(main_selected: SelectionExecutor) -> None:
    result = main_selected.run("SELECT note FROM main.orders", "qualified")
    assert result.rows == ((VISIBLE_SALES,),)


def test_the_qualified_unselected_table_is_rejected(main_selected: SelectionExecutor) -> None:
    with pytest.raises(QueryRejected):
        main_selected.run("SELECT note FROM sales.orders", "probe")


def test_a_unique_bare_selected_name_works(main_selected: SelectionExecutor) -> None:
    assert main_selected.run("SELECT amount FROM refunds", "unique").rows == ((30,),)
