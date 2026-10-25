"""RED tests for T011: SQL validator (read-only allow list, LIMIT, extraction, catalog)."""

from __future__ import annotations

import dataclasses
import importlib
from types import ModuleType

import pytest

DIALECTS = ("postgres", "duckdb")


@pytest.fixture
def v() -> ModuleType:
    """Lazily import the target module so collection succeeds while RED."""
    return importlib.import_module("rootcause_connections.validation")


def _codes(issues) -> set[str]:
    return {i.code.name for i in issues}


ALLOWED = [
    pytest.param("SELECT a, b FROM t", id="plain-select"),
    pytest.param("SELECT a FROM t WHERE a > 1 ORDER BY a DESC", id="where-order-by"),
    pytest.param(
        "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id",
        id="join-aliases",
    ),
    pytest.param("SELECT x.a FROM (SELECT a FROM t) AS x", id="subquery-in-from"),
    pytest.param("WITH x AS (SELECT a FROM t) SELECT a FROM x", id="cte"),
    pytest.param("SELECT a FROM t UNION ALL SELECT a FROM u", id="union-all"),
    pytest.param(
        "SELECT a, row_number() OVER (PARTITION BY b ORDER BY a) AS rn FROM t",
        id="window-function",
    ),
    pytest.param("SELECT b, sum(a) AS s FROM t GROUP BY b", id="group-by-aggregate"),
    pytest.param("SELECT count(*) FROM t", id="scalar-aggregate"),
    pytest.param("SELECT a FROM public.t", id="schema-qualified"),
    pytest.param('SELECT "Id" FROM "Orders"', id="quoted-identifiers"),
    pytest.param("SELECT a FROM t LIMIT 100", id="existing-limit"),
]


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_statement_is_ok_without_errors(v, sql: str, dialect: str) -> None:
    result = v.validate_sql(sql, dialect)

    assert result.ok is True
    assert result.errors == ()
    assert result.sql.strip() != ""


FORBIDDEN = [
    ("INSERT INTO t (a) VALUES (1)", "NOT_READ_ONLY", DIALECTS),
    ("UPDATE t SET a = 1", "NOT_READ_ONLY", DIALECTS),
    ("DELETE FROM t WHERE a = 1", "NOT_READ_ONLY", DIALECTS),
    ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE", "NOT_READ_ONLY", DIALECTS),
    ("CREATE TABLE n (a int)", "NOT_READ_ONLY", DIALECTS),
    ("DROP TABLE t", "NOT_READ_ONLY", DIALECTS),
    ("ALTER TABLE t ADD COLUMN z int", "NOT_READ_ONLY", DIALECTS),
    ("TRUNCATE TABLE t", "NOT_READ_ONLY", DIALECTS),
    ("SELECT a INTO new_table FROM t", "NOT_READ_ONLY", ("postgres",)),
    ("SELECT 1; SELECT 2", "MULTIPLE_STATEMENTS", DIALECTS),
    ("SELECT 1; -- x\nDROP TABLE t", "MULTIPLE_STATEMENTS", DIALECTS),
    (
        "WITH x AS (INSERT INTO t (a) VALUES (1) RETURNING *) SELECT * FROM x",
        "NOT_READ_ONLY",
        ("postgres",),
    ),
    ("SELECT pg_sleep(10)", "FORBIDDEN_FUNCTION", DIALECTS),
    ("COPY t TO '/tmp/x'", "NOT_READ_ONLY", DIALECTS),
    ("SET search_path TO x", "NOT_READ_ONLY", DIALECTS),
    ("CALL proc()", "NOT_READ_ONLY", DIALECTS),
    ("GRANT SELECT ON t TO u", "NOT_READ_ONLY", DIALECTS),
    ("SELEC FROM WHERE ((", "PARSE_ERROR", DIALECTS),
]

FORBIDDEN_PARAMS = [
    pytest.param(sql, code, dialect, id=f"{code}-{dialect}-{sql[:28]!r}")
    for sql, code, dialects in FORBIDDEN
    for dialect in dialects
]


@pytest.mark.unit
@pytest.mark.parametrize(("sql", "code", "dialect"), FORBIDDEN_PARAMS)
def test_forbidden_statement_is_rejected_with_expected_code(
    v, sql: str, code: str, dialect: str
) -> None:
    result = v.validate_sql(sql, dialect)

    assert result.ok is False
    assert code in _codes(result.errors)


@pytest.mark.unit
def test_unsupported_dialect_is_rejected(v) -> None:
    result = v.validate_sql("SELECT 1", "oracle")

    assert result.ok is False
    assert "UNSUPPORTED_DIALECT" in _codes(result.errors)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_non_aggregate_select_without_limit_gets_default_limit(v, dialect: str) -> None:
    result = v.validate_sql("SELECT a FROM t", dialect)

    assert result.ok is True
    assert result.limit_injected is True
    assert result.sql.upper().rstrip("; \n").endswith("LIMIT 10000")


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_existing_limit_below_default_is_kept(v, dialect: str) -> None:
    result = v.validate_sql("SELECT a FROM t LIMIT 50", dialect)

    assert result.limit_injected is False
    assert "LIMIT 50" in result.sql.upper()
    assert "10000" not in result.sql
    assert result.warnings == ()


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_limit_above_default_is_capped_with_warning(v, dialect: str) -> None:
    result = v.validate_sql("SELECT a FROM t LIMIT 50000", dialect)

    assert result.ok is True
    assert "LIMIT 10000" in result.sql.upper()
    assert "50000" not in result.sql
    assert len(result.warnings) >= 1


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_group_by_aggregate_is_not_limited(v, dialect: str) -> None:
    result = v.validate_sql("SELECT b, sum(a) FROM t GROUP BY b", dialect)

    assert result.limit_injected is False
    assert "LIMIT" not in result.sql.upper()


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_scalar_aggregate_is_not_limited(v, dialect: str) -> None:
    result = v.validate_sql("SELECT count(*) FROM t", dialect)

    assert result.limit_injected is False
    assert "LIMIT" not in result.sql.upper()


@pytest.mark.unit
def test_custom_default_limit_is_honoured(v) -> None:
    result = v.validate_sql("SELECT a FROM t", "postgres", default_limit=500)

    assert result.limit_injected is True
    assert result.sql.upper().rstrip("; \n").endswith("LIMIT 500")


@pytest.mark.unit
def test_custom_default_limit_caps_larger_existing_limit(v) -> None:
    result = v.validate_sql("SELECT a FROM t LIMIT 900", "postgres", default_limit=500)

    assert "LIMIT 500" in result.sql.upper()
    assert len(result.warnings) >= 1


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_referenced_tables_keep_first_appearance_order_and_case(v, dialect: str) -> None:
    sql = "SELECT o.id, c.name FROM public.orders o JOIN customers c ON c.id = o.cid"

    result = v.validate_sql(sql, dialect)

    assert result.referenced_tables == ("public.orders", "customers")


@pytest.mark.unit
def test_referenced_tables_preserve_case_as_written(v) -> None:
    result = v.validate_sql("SELECT a FROM Public.Orders", "postgres")

    assert result.referenced_tables == ("Public.Orders",)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_referenced_columns_are_qualified_when_available(v, dialect: str) -> None:
    sql = "SELECT o.id, c.name, total FROM orders o JOIN customers c ON c.id = o.cid"

    result = v.validate_sql(sql, dialect)

    assert "o.id" in result.referenced_columns
    assert "c.name" in result.referenced_columns
    assert "total" in result.referenced_columns


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_cte_names_are_not_reported_as_tables(v, dialect: str) -> None:
    sql = "WITH recent AS (SELECT id FROM orders) SELECT id FROM recent"

    result = v.validate_sql(sql, dialect)

    assert result.referenced_tables == ("orders",)
    assert "recent" not in result.referenced_tables


@pytest.fixture
def catalog(v):
    return v.CatalogSnapshot({"orders": ("id", "total"), "customers": ("id", "name")})


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_catalog_known_table_and_columns_pass(v, catalog, dialect: str) -> None:
    result = v.validate_sql("SELECT id, total FROM orders", dialect, catalog=catalog)

    assert result.ok is True
    assert result.errors == ()


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_catalog_unknown_column_is_error(v, catalog, dialect: str) -> None:
    result = v.validate_sql("SELECT nope FROM orders", dialect, catalog=catalog)

    assert result.ok is False
    assert "UNKNOWN_COLUMN" in _codes(result.errors)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_catalog_unknown_table_is_error(v, catalog, dialect: str) -> None:
    result = v.validate_sql("SELECT * FROM missing", dialect, catalog=catalog)

    assert result.ok is False
    assert "UNKNOWN_TABLE" in _codes(result.errors)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_catalog_matching_is_case_insensitive(v, catalog, dialect: str) -> None:
    result = v.validate_sql("SELECT ID FROM Orders", dialect, catalog=catalog)

    assert result.ok is True
    assert result.errors == ()


@pytest.mark.unit
@pytest.mark.parametrize("dialect", DIALECTS)
def test_catalog_ambiguous_bare_column_is_warning_not_error(v, catalog, dialect: str) -> None:
    sql = "SELECT id FROM orders o JOIN customers c ON c.id = o.id"

    result = v.validate_sql(sql, dialect, catalog=catalog)

    assert result.ok is True
    assert result.errors == ()
    assert len(result.warnings) >= 1


@pytest.mark.unit
def test_without_catalog_unknown_names_are_not_flagged(v) -> None:
    result = v.validate_sql("SELECT nope FROM missing", "postgres")

    assert result.ok is True


@pytest.mark.unit
def test_postgres_ilike_transpiles_to_parseable_duckdb(v) -> None:
    sqlglot = pytest.importorskip("sqlglot")

    result = v.validate_sql("SELECT a FROM t WHERE a ILIKE '%x%'", "duckdb")

    assert result.ok is True
    assert sqlglot.parse_one(result.sql, read="duckdb") is not None


@pytest.mark.unit
def test_validation_result_is_frozen_and_uses_tuples(v) -> None:
    result = v.validate_sql("SELECT a FROM t", "postgres")

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.ok = False  # type: ignore[misc]
    assert isinstance(result.errors, tuple)
    assert isinstance(result.warnings, tuple)
    assert isinstance(result.referenced_tables, tuple)
    assert isinstance(result.referenced_columns, tuple)


@pytest.mark.unit
def test_failed_result_also_uses_tuples(v) -> None:
    result = v.validate_sql("DROP TABLE t", "postgres")

    assert isinstance(result.errors, tuple)
    assert isinstance(result.warnings, tuple)
    assert all(isinstance(i, v.ValidationIssue) for i in result.errors)


@pytest.mark.unit
def test_issue_code_enum_exposes_planned_members(v) -> None:
    names = {m.name for m in v.IssueCode}

    assert {
        "MULTIPLE_STATEMENTS",
        "NOT_READ_ONLY",
        "FORBIDDEN_FUNCTION",
        "PARSE_ERROR",
        "UNKNOWN_TABLE",
        "UNKNOWN_COLUMN",
        "UNSUPPORTED_DIALECT",
    } <= names
