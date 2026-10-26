"""RED tests pinning the hardened SQL validator contract (allow-listed functions, LIMIT caps,
robustness, output hygiene, scope-aware references, schema-aware catalog)."""

from __future__ import annotations

import importlib
import re
from types import ModuleType
from typing import Any

import pytest

DEFAULT_LIMIT = 10_000
MAX_INPUT_CHARS = 20_000
PG_DUCK = ("postgres", "duckdb")


@pytest.fixture
def v() -> ModuleType:
    """Lazily import the target module so collection never fails."""
    return importlib.import_module("rootcause.connections.validation")


def _codes(issues) -> set[str]:
    return {i.code.name for i in issues}


def _cases(rows: list[tuple[str, str]]) -> list[Any]:
    return [pytest.param(d, sql, id=f"{d}-{sql[:45]!r}") for d, sql in rows]


# ---------------------------------------------------------------- A. functions

SCALAR_FUNCTION_REJECTS = _cases(
    [
        ("postgres", "SELECT dblink_exec('host=x','DROP TABLE t')"),
        ("postgres", "SELECT * FROM dblink('host=x','DELETE FROM t') AS r(a int)"),
        ("postgres", "SELECT set_config('statement_timeout','0',false)"),
        ("postgres", "SELECT query_to_xml('DELETE FROM t RETURNING *', true, false, '')"),
        ("postgres", "SELECT database_to_xml(true, true, '')"),
        ("postgres", "SELECT lo_create(0)"),
        ("postgres", "SELECT lo_unlink(1234)"),
        ("postgres", "SELECT setval('seq', 1)"),
        ("postgres", "SELECT nextval('seq')"),
        ("postgres", "SELECT pg_notify('c','x')"),
        ("postgres", "SELECT pg_advisory_lock(1)"),
        ("postgres", "SELECT pg_try_advisory_lock(1)"),
        ("postgres", "SELECT pg_stat_file('/etc/passwd')"),
        ("postgres", "SELECT * FROM pg_ls_waldir()"),
        ("postgres", "SELECT pg_reload_conf()"),
        ("postgres", "SELECT pg_sleep(10)"),
        ("postgres", 'SELECT "PG_SLEEP"(10)'),
        ("postgres", "SELECT pg_read_file('/etc/passwd')"),
        ("postgres", "SELECT lo_import('/etc/passwd')"),
        ("postgres", "SELECT myschema.delete_everything()"),
        ("postgres", "SELECT pg_catalog.pg_sleep(10)"),
        ("postgres", 'SELECT "pg_catalog"."pg_sleep"(10)'),
        ("postgres", "SELECT * FROM pg_sleep(10)"),
        ("postgres", "SELECT * FROM t, LATERAL pg_sleep(1)"),
        ("postgres", "SELECT 1 FROM t WHERE pg_sleep(1) IS NULL"),
        ("postgres", "SELECT 1 FROM t ORDER BY pg_sleep(1)"),
        ("postgres", "SELECT (pg_sleep(1))::text"),
        ("postgres", "SELECT totally_unknown_function(1)"),
        ("duckdb", "SELECT * FROM read_csv('/etc/passwd')"),
        ("duckdb", "SELECT * FROM read_csv_auto('/etc/passwd')"),
        ("duckdb", "SELECT * FROM read_text('/etc/passwd')"),
        ("duckdb", "SELECT * FROM read_blob('/etc/passwd')"),
        ("duckdb", "SELECT * FROM read_json('/etc/passwd')"),
        ("duckdb", "SELECT * FROM read_parquet('s3://b/x')"),
        ("duckdb", "SELECT * FROM glob('/*')"),
        ("duckdb", "SELECT getenv('HOME')"),
        ("duckdb", "SELECT * FROM duckdb_secrets()"),
        ("duckdb", "SELECT * FROM query('DELETE FROM t RETURNING *')"),
        ("duckdb", "SELECT * FROM query_table('t')"),
        ("duckdb", "SELECT * FROM postgres_execute('db','DROP TABLE t')"),
        ("duckdb", "SELECT * FROM postgres_scan('host=x','public','t')"),
        ("duckdb", "SELECT * FROM sqlite_scan('/tmp/x.db','t')"),
        ("duckdb", "SELECT sleep_ms(1)"),
        ("duckdb", "SELECT (SELECT count(*) FROM read_csv('/etc/passwd'))"),
        ("mysql", "SELECT sys_exec('id')"),
        ("mysql", "SELECT get_lock('x',1)"),
        ("mysql", "SELECT SLEEP(10)"),
        ("mysql", "SELECT LOAD_FILE('/etc/passwd')"),
        ("mysql", "SELECT totally_unknown_function(1)"),
    ]
)


@pytest.mark.unit
@pytest.mark.parametrize(("dialect", "sql"), SCALAR_FUNCTION_REJECTS)
def test_dangerous_or_unknown_function_is_rejected_as_forbidden(v, dialect: str, sql: str) -> None:
    result = v.validate_sql(sql, dialect)

    assert result.ok is False
    assert "FORBIDDEN_FUNCTION" in _codes(result.errors)


OTHER_REJECTS = _cases(
    [
        ("duckdb", "SELECT * FROM '/etc/passwd'"),
        ("duckdb", 'SELECT * FROM "/etc/passwd"'),
        ("duckdb", "SELECT * FROM 'data.csv'"),
        ("duckdb", "SELECT * FROM 's3://bucket/x.parquet'"),
        ("mysql", "/*+ MAX_EXECUTION_TIME(0) */ SELECT 1"),
        ("mysql", "SELECT /*+ MAX_EXECUTION_TIME(0) */ 1"),
        ("mysql", "/*! SLEEP(10) */"),
        ("mysql", "SELECT /*! SLEEP(10) */ 1"),
        ("tsql", "SELECT * FROM OPENQUERY(x,'EXEC xp_cmdshell ''dir''')"),
        ("tsql", "SELECT * FROM OPENROWSET('SQLNCLI','s','q')"),
        ("tsql", "SELECT * FROM OPENROWSET(BULK '/etc/passwd', SINGLE_CLOB) AS x"),
        ("tsql", "SELECT * FROM OPENDATASOURCE('p','s').d.s.t"),
        ("tsql", "SELECT * FROM t WITH (UPDLOCK)"),
        ("tsql", "SELECT * FROM t WITH (XLOCK)"),
        ("tsql", "SELECT * FROM t WITH (TABLOCKX, HOLDLOCK)"),
        ("bigquery", "SELECT * FROM EXTERNAL_QUERY('c','DELETE FROM t')"),
        ("snowflake", "SELECT SYSTEM$CANCEL_ALL_QUERIES()"),
        ("snowflake", "SELECT SYSTEM$WAIT(10)"),
        ("snowflake", "SELECT $1 FROM @stage/file.csv"),
        ("clickhouse", "SELECT * FROM file('/etc/passwd')"),
        ("clickhouse", "SELECT * FROM url('http://169.254.169.254/')"),
        ("clickhouse", "SELECT * FROM s3('x')"),
        ("clickhouse", "SELECT * FROM remote('h','db','t')"),
        ("clickhouse", "SELECT * FROM executable('x')"),
        ("clickhouse", "SELECT 1 SETTINGS readonly=0"),
        ("clickhouse", "SELECT 1 SETTINGS max_execution_time=0"),
        ("clickhouse", "SELECT sleepEachRow(1)"),
        # statement-shape probes carried over from the security review
        ("postgres", "SELECT * FROM t FOR UPDATE"),
        ("postgres", "SELECT * FROM t FOR SHARE"),
        ("postgres", "SELECT * FROM (SELECT * FROM t FOR UPDATE) s"),
        ("postgres", "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d"),
        ("postgres", "WITH d AS (UPDATE t SET a=1 RETURNING *) SELECT * FROM d"),
        ("postgres", "SELECT * FROM (INSERT INTO t VALUES (1) RETURNING *) x"),
        ("postgres", "SELECT * INTO newt FROM t"),
        ("postgres", "EXPLAIN ANALYZE DELETE FROM t"),
        ("postgres", "DO $$ BEGIN DELETE FROM t; END $$"),
        ("postgres", "COPY t TO PROGRAM 'rm -rf /'"),
        ("postgres", "SELECT 1; DELETE FROM t"),
        ("postgres", "SELECT 1 /* x */; DROP TABLE t -- y"),
        ("postgres", "SELECT 1\x00; DELETE FROM t"),
        ("postgres", "SELECT 1； DELETE FROM t"),
        ("mysql", "SELECT * FROM t FOR UPDATE"),
        ("mysql", "SELECT * FROM t LOCK IN SHARE MODE"),
        ("mysql", "SELECT * FROM t INTO OUTFILE '/tmp/x'"),
        ("mysql", "SELECT 1 /*!50000 ; DELETE FROM t */"),
        ("tsql", "SELECT 1\nDELETE FROM t"),
        ("tsql", "SELECT 1 AS x DELETE FROM t"),
        ("tsql", "SELECT * INTO #x FROM t"),
        ("tsql", "EXEC xp_cmdshell 'dir'"),
        ("duckdb", "ATTACH '/tmp/x.db' AS x"),
        ("duckdb", "INSTALL httpfs"),
        ("duckdb", "PRAGMA enable_profiling"),
        ("snowflake", "COPY INTO @stage FROM t"),
        ("clickhouse", "SELECT 1 INTO OUTFILE '/tmp/x'"),
    ]
)


@pytest.mark.unit
@pytest.mark.parametrize(("dialect", "sql"), OTHER_REJECTS)
def test_dangerous_construct_is_rejected(v, dialect: str, sql: str) -> None:
    result = v.validate_sql(sql, dialect)

    assert result.ok is False
    assert result.errors != ()


@pytest.mark.unit
@pytest.mark.parametrize("dialect", ("duckdb", "snowflake"))
def test_file_url_and_stage_table_names_are_not_read_only_or_forbidden(v, dialect: str) -> None:
    sql = "SELECT * FROM '/etc/passwd'" if dialect == "duckdb" else "SELECT $1 FROM @stage/f.csv"

    result = v.validate_sql(sql, dialect)

    assert result.ok is False
    assert _codes(result.errors) & {"NOT_READ_ONLY", "FORBIDDEN_FUNCTION"}


ALLOWED_BOTH = [
    "SELECT count(*), sum(x), avg(x), min(x), max(x) FROM t GROUP BY y",
    "SELECT lower(upper(trim(s))) FROM t",
    "SELECT coalesce(a, b) FROM t",
    "SELECT cast(x AS int) FROM t",
    "SELECT date_trunc('day', ts) FROM t",
    "SELECT now()",
    "SELECT current_date",
    "SELECT extract(year FROM ts) FROM t",
    "SELECT row_number() OVER (PARTITION BY a ORDER BY b) FROM t",
    "SELECT CASE WHEN a THEN 1 ELSE 0 END FROM t",
    "SELECT length(s) FROM t",
    "SELECT concat(a, b) FROM t",
    "SELECT round(x, 2) FROM t",
    "SELECT abs(x) FROM t",
    "SELECT nullif(a, 0), greatest(a, b), least(a, b) FROM t",
]
ALLOWED_PARAMS = [
    pytest.param(d, sql, id=f"{d}-{sql[:45]!r}") for sql in ALLOWED_BOTH for d in PG_DUCK
] + [
    pytest.param("postgres", "SELECT substring(s FROM 1 FOR 2) FROM t", id="pg-substring-from-for"),
    pytest.param("postgres", "SELECT string_agg(s, ',') FROM t", id="pg-string-agg"),
    pytest.param(
        "postgres", "SELECT * FROM unnest(ARRAY[1, 2, 3]) AS u(x)", id="pg-unnest-in-from"
    ),
    pytest.param("duckdb", "SELECT * FROM unnest([1, 2, 3]) AS u(x)", id="duckdb-unnest-in-from"),
    pytest.param("postgres", "SELECT * FROM generate_series(1, 10)", id="pg-generate-series"),
    pytest.param("duckdb", "SELECT * FROM range(10)", id="duckdb-range"),
    pytest.param("duckdb", "SELECT json_extract(j, '$.a') FROM t", id="duckdb-json-extract"),
    pytest.param("mysql", "SELECT json_extract(j, '$.a') FROM t", id="mysql-json-extract"),
    pytest.param(
        "postgres",
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY x) FROM t",
        id="pg-percentile-cont",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("dialect", "sql"), ALLOWED_PARAMS)
def test_safe_function_families_remain_accepted(v, dialect: str, sql: str) -> None:
    result = v.validate_sql(sql, dialect)

    assert result.ok is True
    assert result.errors == ()


# ---------------------------------------------------------------- B. LIMIT


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT b, sum(a) FROM t GROUP BY b", id="group-by"),
        pytest.param("SELECT count(*) FROM t", id="scalar-aggregate"),
        pytest.param("SELECT a FROM t UNION ALL SELECT a FROM u", id="union"),
    ],
)
def test_limit_is_always_injected_when_absent(v, sql: str, dialect: str) -> None:
    result = v.validate_sql(sql, dialect)

    assert result.ok is True
    assert result.limit_injected is True
    assert result.sql.upper().rstrip("; \n").endswith(f"LIMIT {DEFAULT_LIMIT}")


UNSAFE_LIMITS = [
    pytest.param("postgres", "SELECT a FROM t LIMIT ALL", id="limit-all"),
    pytest.param("postgres", "SELECT a FROM t LIMIT NULL", id="limit-null"),
    pytest.param("postgres", "SELECT a FROM t LIMIT 10+99999999", id="limit-arith"),
    pytest.param("postgres", "SELECT a FROM t LIMIT $1", id="limit-param"),
    pytest.param("postgres", "SELECT a FROM t LIMIT (SELECT 99999999)", id="limit-subquery"),
    pytest.param("postgres", "SELECT a FROM t LIMIT CAST(99999999 AS INT)", id="limit-cast"),
    pytest.param("postgres", "SELECT a FROM t LIMIT 1e9", id="limit-float"),
    pytest.param("postgres", "SELECT a FROM t LIMIT -1", id="limit-negative"),
    pytest.param("postgres", "SELECT a FROM t LIMIT 99999999999999999999999", id="limit-huge"),
    pytest.param("postgres", "SELECT a FROM t LIMIT 10001", id="limit-just-above-cap"),
    pytest.param("duckdb", "SELECT a FROM t LIMIT 100%", id="duckdb-limit-percent"),
    pytest.param("duckdb", "SELECT a FROM t LIMIT 100 PERCENT", id="duckdb-limit-percent-word"),
    pytest.param("tsql", "SELECT TOP 100 PERCENT a FROM t", id="tsql-top-percent"),
    pytest.param("tsql", "SELECT TOP 99999999 a FROM t", id="tsql-top-huge"),
    pytest.param("postgres", "SELECT a FROM t FETCH FIRST 99999999 ROWS ONLY", id="fetch-first"),
    pytest.param(
        "postgres",
        "SELECT a FROM t OFFSET 5 ROWS FETCH NEXT 99999999 ROWS ONLY",
        id="offset-fetch-next",
    ),
    pytest.param(
        "postgres", "SELECT a FROM t UNION ALL SELECT a FROM u LIMIT 999999999", id="union-limit"
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("dialect", "sql"), UNSAFE_LIMITS)
def test_unsafe_limit_is_replaced_by_default_with_capped_warning(v, dialect: str, sql: str) -> None:
    result = v.validate_sql(sql, dialect)

    assert result.ok is True
    assert str(DEFAULT_LIMIT) in result.sql
    upper = result.sql.upper()
    assert re.search(r"LIMIT\s+(ALL|NULL)", upper) is None
    for leaked in ("99999999", "PERCENT", "1E9", "$1", "-1"):
        assert leaked not in upper
    assert "LIMIT_CAPPED" in _codes(result.warnings)


SAFE_LIMITS = [
    pytest.param("SELECT a FROM t LIMIT 0", id="zero"),
    pytest.param("SELECT a FROM t LIMIT 1", id="one"),
    pytest.param(f"SELECT a FROM t LIMIT {DEFAULT_LIMIT}", id="exactly-cap"),
    pytest.param("SELECT a FROM t FETCH FIRST 5 ROWS ONLY", id="fetch-within-cap"),
]


@pytest.mark.unit
@pytest.mark.parametrize("sql", SAFE_LIMITS)
def test_limit_within_cap_is_kept_without_warning(v, sql: str) -> None:
    result = v.validate_sql(sql, "postgres")

    assert result.ok is True
    assert result.limit_injected is False
    assert "LIMIT_CAPPED" not in _codes(result.warnings)


# ---------------------------------------------------------------- D. robustness


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_input_over_character_cap_is_rejected_as_too_large(v, dialect: str) -> None:
    sql = "SELECT '" + "x" * MAX_INPUT_CHARS + "'"

    result = v.validate_sql(sql, dialect)

    assert result.ok is False
    assert "INPUT_TOO_LARGE" in _codes(result.errors)


@pytest.mark.unit
def test_ten_megabyte_literal_is_rejected_without_exception(v) -> None:
    result = v.validate_sql("SELECT '" + "x" * 10_000_000 + "'", "postgres")

    assert result.ok is False
    assert "INPUT_TOO_LARGE" in _codes(result.errors)


@pytest.mark.unit
def test_input_exactly_at_character_cap_is_not_too_large(v) -> None:
    sql = "SELECT '" + "x" * (MAX_INPUT_CHARS - len("SELECT ''")) + "'"
    assert len(sql) == MAX_INPUT_CHARS

    result = v.validate_sql(sql, "postgres")

    assert "INPUT_TOO_LARGE" not in _codes(result.errors)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_deeply_nested_parentheses_are_rejected_without_exception(v, dialect: str) -> None:
    sql = "SELECT " + "(" * 200 + "1" + ")" * 200

    result = v.validate_sql(sql, dialect)

    assert result.ok is False
    assert _codes(result.errors) & {"PARSE_ERROR", "INPUT_TOO_LARGE"}


@pytest.mark.unit
@pytest.mark.parametrize("depth", (300, 1000))
def test_deeply_nested_case_never_raises(v, depth: int) -> None:
    sql = "SELECT " + "CASE WHEN 1=1 THEN " * depth + "1" + " END" * depth

    result = v.validate_sql(sql, "postgres")

    assert isinstance(result.ok, bool)


@pytest.mark.unit
@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT " + "abs(" * 200 + "1" + ")" * 200, id="nested-functions"),
        pytest.param(
            "SELECT * FROM " + "(SELECT * FROM " * 200 + "t" + ") s" * 200, id="nested-subq"
        ),
        pytest.param("SELECT 1" + ";" * 5000, id="many-semicolons"),
    ],
)
def test_pathological_shapes_never_raise(v, sql: str) -> None:
    result = v.validate_sql(sql, "postgres")

    assert isinstance(result.ok, bool)


FUZZ_INPUTS = [
    pytest.param("", id="empty"),
    pytest.param("   \n\t  ", id="whitespace"),
    pytest.param("".join(chr(i) for i in range(256)), id="latin1-garbage"),
    pytest.param("\x00\x00\x00", id="nul-bytes"),
    pytest.param("\ud800", id="lone-surrogate"),
    pytest.param("SELECT '\U0001f600' AS é", id="emoji-and-accent"),
    pytest.param("SELECT 'it''s'; --", id="sql-metacharacters"),
    pytest.param("((((", id="unbalanced-parens"),
    pytest.param("'" * 1000, id="unterminated-quotes"),
]


@pytest.mark.unit
@pytest.mark.parametrize("dialect", ("postgres", "duckdb", "mysql", "tsql"))
@pytest.mark.parametrize("sql", FUZZ_INPUTS)
def test_validate_sql_never_raises_for_any_string(v, sql: str, dialect: str) -> None:
    result = v.validate_sql(sql, dialect)

    assert isinstance(result.ok, bool)
    assert isinstance(result.errors, tuple)


@pytest.mark.unit
@pytest.mark.parametrize("sql", ("", "   \n\t "))
def test_empty_or_blank_input_is_rejected(v, sql: str) -> None:
    result = v.validate_sql(sql, "postgres")

    assert result.ok is False


# ---------------------------------------------------------------- E. output hygiene


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_comments_are_stripped_from_output_sql(v, dialect: str) -> None:
    result = v.validate_sql("SELECT a /* hi */ FROM t -- x", dialect)

    assert result.ok is True
    assert "/*" not in result.sql
    assert "--" not in result.sql


@pytest.mark.unit
def test_line_comment_cannot_swallow_injected_limit(v) -> None:
    result = v.validate_sql("SELECT a FROM t -- trailing note", "postgres")

    assert result.ok is True
    assert "--" not in result.sql
    assert result.sql.upper().rstrip("; \n").endswith(f"LIMIT {DEFAULT_LIMIT}")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        pytest.param("mysql", "SELECT 1 /*!50000 + 1 */", id="mysql-versioned-comment"),
        pytest.param("mysql", "SELECT 1 -- */ ; DROP TABLE t; /*\n", id="mysql-comment-injection"),
        pytest.param("postgres", "SELECT 1 /* a /* b */ ; DROP TABLE t; /* c */", id="pg-nested"),
        pytest.param("postgres", 'SELECT U&"d\\0061ta" FROM t', id="pg-unicode-escape-ident"),
        pytest.param("postgres", "SELECT U&'d\\0061ta' FROM t", id="pg-unicode-escape-string"),
    ],
)
def test_accepted_output_never_contains_executable_comment_or_unicode_escape(
    v, dialect: str, sql: str
) -> None:
    result = v.validate_sql(sql, dialect)

    if result.ok:
        assert "/*!" not in result.sql
        assert "/*" not in result.sql
        assert "--" not in result.sql
        assert "U&" not in result.sql


@pytest.mark.unit
def test_mysql_optimizer_hint_node_is_rejected(v) -> None:
    result = v.validate_sql("SELECT /*+ MAX_EXECUTION_TIME(0) */ a FROM t", "mysql")

    assert result.ok is False


# ---------------------------------------------------------------- F. references


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_schema_qualified_name_is_not_hidden_by_same_named_cte(v, dialect: str) -> None:
    sql = "WITH recent AS (SELECT 1) SELECT * FROM hr.recent"

    result = v.validate_sql(sql, dialect)

    assert result.referenced_tables == ("hr.recent",)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_physical_table_is_reported_when_derived_alias_reuses_its_name(v, dialect: str) -> None:
    sql = "SELECT * FROM salaries s JOIN (SELECT 1 AS id) AS salaries ON true"

    result = v.validate_sql(sql, dialect)

    assert result.referenced_tables == ("salaries",)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_cte_shadowing_its_own_source_reports_inner_physical_table(v, dialect: str) -> None:
    sql = "WITH orders AS (SELECT * FROM orders) SELECT * FROM orders"

    result = v.validate_sql(sql, dialect)

    assert result.referenced_tables == ("orders",)


@pytest.mark.unit
def test_table_function_does_not_produce_empty_table_name(v) -> None:
    result = v.validate_sql("SELECT * FROM generate_series(1, 10)", "postgres")

    assert result.ok is True
    assert "" not in result.referenced_tables


# ---------------------------------------------------------------- G. catalog


@pytest.fixture
def catalog(v):
    return v.CatalogSnapshot({"orders": ("id", "total"), "customers": ("id", "name")})


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT id AS x FROM orders ORDER BY x", id="order-by-select-alias"),
        pytest.param("SELECT o.* FROM orders o", id="qualified-star"),
        pytest.param(
            "SELECT id FROM orders WHERE id IN (SELECT id FROM customers)", id="in-subquery"
        ),
    ],
)
def test_catalog_valid_shapes_pass_without_ambiguity_warning(
    v, catalog, sql: str, dialect: str
) -> None:
    result = v.validate_sql(sql, dialect, catalog=catalog)

    assert result.ok is True
    assert "AMBIGUOUS_COLUMN" not in _codes(result.warnings)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_catalog_unknown_qualifier_is_error(v, catalog, dialect: str) -> None:
    result = v.validate_sql("SELECT zz.nope FROM orders", dialect, catalog=catalog)

    assert result.ok is False
    assert _codes(result.errors) & {"UNKNOWN_TABLE", "UNKNOWN_COLUMN"}


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_catalog_unknown_column_is_not_masked_by_derived_table(v, catalog, dialect: str) -> None:
    sql = "SELECT nope FROM orders o JOIN (SELECT 1 AS k) s ON true"

    result = v.validate_sql(sql, dialect, catalog=catalog)

    assert result.ok is False
    assert "UNKNOWN_COLUMN" in _codes(result.errors)


@pytest.fixture
def schema_catalog(v):
    return v.CatalogSnapshot({"sales.orders": ("id",), "hr.orders": ("secret",)})


@pytest.mark.unit
def test_catalog_wrong_schema_for_known_bare_name_is_unknown_table(v, schema_catalog) -> None:
    result = v.validate_sql("SELECT * FROM public.orders", "postgres", catalog=schema_catalog)

    assert result.ok is False
    assert "UNKNOWN_TABLE" in _codes(result.errors)


@pytest.mark.unit
def test_catalog_unqualified_name_in_two_schemas_is_ambiguous_not_silently_merged(
    v, schema_catalog
) -> None:
    result = v.validate_sql("SELECT secret FROM orders", "postgres", catalog=schema_catalog)

    flagged = "AMBIGUOUS_TABLE" in _codes(result.warnings) or "AMBIGUOUS_TABLE" in _codes(
        result.errors
    )
    assert result.ok is False or flagged


@pytest.mark.unit
def test_catalog_schema_qualified_name_resolves_exactly(v, schema_catalog) -> None:
    result = v.validate_sql("SELECT id FROM sales.orders", "postgres", catalog=schema_catalog)

    assert result.ok is True
    assert result.errors == ()


@pytest.mark.unit
def test_catalog_schema_less_keys_accept_any_schema_prefix(v) -> None:
    flat = v.CatalogSnapshot({"orders": ("id",)})

    result = v.validate_sql("SELECT id FROM public.orders", "postgres", catalog=flat)

    assert result.ok is True


# ---------------------------------------------------------------- C. enum


@pytest.mark.unit
def test_new_issue_codes_exist(v) -> None:
    names = {m.name for m in v.IssueCode}

    assert {"LIMIT_CAPPED", "AMBIGUOUS_COLUMN", "AMBIGUOUS_TABLE", "INPUT_TOO_LARGE"} <= names


# ---------------------------------------------------------------- D. class-level allow list

CLASS_LEVEL_REJECTS = _cases(
    [
        ("tsql", "SELECT IF(1=1) EXEC xp_cmdshell 'dir'"),
        ("tsql", "SELECT IF(1=1) EXECUTE('DROP TABLE t')"),
        ("tsql", "SELECT IF(1=1) SHUTDOWN"),
        ("tsql", "SELECT IF(1=1) sp_configure 'x'"),
        ("tsql", "SELECT IF(1=1) DBCC CHECKDB"),
        ("tsql", "SELECT IF(1=1) WAITFOR DELAY '00:00:05'"),
        ("postgres", "SELECT a OPERATOR(myschema.===) b FROM t"),
        ("postgres", "SELECT CAST(a AS myschema.evil_type) FROM t"),
        ("snowflake", "SELECT * FROM TABLE('other_db.secret.t')"),
        ("snowflake", "SELECT * FROM TABLE($tbl)"),
        ("snowflake", "SELECT * FROM TABLE(DIRECTORY(@stage))"),
        ("snowflake", "SELECT DIRECTORY(@stage)"),
        ("snowflake", "SELECT $var"),
    ]
)


@pytest.mark.unit
@pytest.mark.parametrize(("dialect", "sql"), CLASS_LEVEL_REJECTS)
def test_class_level_allow_list_rejects_unlisted_nodes(v, dialect: str, sql: str) -> None:
    result = v.validate_sql(sql, dialect)

    assert result.ok is False
    assert _codes(result.errors) & {"NOT_READ_ONLY", "FORBIDDEN_FUNCTION"}


@pytest.mark.unit
def test_clickhouse_limit_by_is_rejected_or_keeps_by_clause_when_capped(v) -> None:
    result = v.validate_sql("SELECT a FROM t LIMIT 999999999 BY a", "clickhouse")

    assert result.ok is False or (
        "BY a" in result.sql and "10000" in result.sql and "999999999" not in result.sql
    )


# ---------------------------------------------------------------- E. duckdb table names

DUCKDB_FILE_LIKE_REJECTS = [
    "SELECT * FROM 'data.csv.gz'",
    "SELECT * FROM 'data.ndjson'",
    "SELECT * FROM 'data.xlsx'",
    'SELECT * FROM "data.parquet.zst"',
    'SELECT * FROM "data.arrow"',
    'SELECT * FROM "data.avro"',
    'SELECT * FROM "data．csv"',
]


@pytest.mark.unit
@pytest.mark.parametrize("sql", DUCKDB_FILE_LIKE_REJECTS)
def test_duckdb_file_like_table_names_are_rejected(v, sql: str) -> None:
    result = v.validate_sql(sql, "duckdb")

    assert result.ok is False
    assert "NOT_READ_ONLY" in _codes(result.errors)


@pytest.mark.unit
@pytest.mark.parametrize("sql", ["SELECT * FROM orders", "SELECT * FROM main.orders"])
def test_duckdb_plain_and_schema_qualified_identifiers_stay_accepted(v, sql: str) -> None:
    result = v.validate_sql(sql, "duckdb")

    assert result.ok is True


@pytest.mark.unit
def test_postgres_schema_qualified_identifier_stays_accepted(v) -> None:
    assert v.validate_sql("SELECT * FROM public.orders", "postgres").ok is True


# ---------------------------------------------------------------- F. correlated subqueries


@pytest.fixture
def correlated_catalog(v):
    return v.CatalogSnapshot({"orders": ("id", "cid", "total"), "customers": ("id",)})


_CORRELATED = (
    "SELECT id FROM orders o WHERE EXISTS "
    "(SELECT 1 FROM customers c WHERE c.id = o.cid AND {col} > 0)"
)


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_correlated_subquery_bare_column_resolves_to_outer_scope(
    v, correlated_catalog, dialect: str
) -> None:
    result = v.validate_sql(_CORRELATED.format(col="total"), dialect, catalog=correlated_catalog)

    assert result.ok is True
    assert result.errors == ()


@pytest.mark.unit
@pytest.mark.parametrize("dialect", PG_DUCK)
def test_correlated_subquery_unknown_bare_column_is_error(
    v, correlated_catalog, dialect: str
) -> None:
    result = v.validate_sql(_CORRELATED.format(col="nope"), dialect, catalog=correlated_catalog)

    assert result.ok is False
    assert "UNKNOWN_COLUMN" in _codes(result.errors)


# ---------------------------------------------------------------- G. executable comments


@pytest.mark.unit
def test_comment_marker_inside_string_literal_is_not_an_executable_comment(v) -> None:
    result = v.validate_sql("SELECT note FROM t WHERE note LIKE '%/*+%'", "postgres")

    assert result.ok is True
    assert "'%/*+%'" in result.sql


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dialect", "sql"),
    [("postgres", "/*+ HINT */ SELECT 1"), ("mysql", "/*M! SELECT 1 */ SELECT 1")],
)
def test_real_executable_comments_are_still_rejected(v, dialect: str, sql: str) -> None:
    assert v.validate_sql(sql, dialect).ok is False


# ---------------------------------------------------------------- H. immutable allow lists


@pytest.mark.unit
@pytest.mark.parametrize("name", ["ALLOWED_ANONYMOUS", "TABLE_FUNCTION_NAMES"])
def test_allow_list_containers_are_immutable(name: str) -> None:
    from types import MappingProxyType

    allowlist = importlib.import_module("rootcause.connections.validation.allowlist")
    container = getattr(allowlist, name)

    assert isinstance(container, MappingProxyType)
    assert all(isinstance(val, frozenset) for val in container.values())
    with pytest.raises(TypeError):
        container["evil"] = frozenset({"x"})  # type: ignore[index]


# ---------------------------------------------------------------- I. tsql keyword aliases

TSQL_KEYWORD_ALIASES = [
    "reconfigure",
    "checkpoint",
    "print",
    "raiserror",
    "deny",
    "bulk",
    "openquery",
    "throw",
    "goto",
    "commit",
    "rollback",
    "delete",
]


@pytest.mark.unit
@pytest.mark.parametrize("alias", TSQL_KEYWORD_ALIASES)
def test_tsql_unquoted_statement_keyword_alias_is_rejected(v, alias: str) -> None:
    result = v.validate_sql(f"SELECT 1 {alias}", "tsql")

    assert result.ok is False


NON_TSQL_ALIAS_DIALECTS = ("postgres", "duckdb", "mysql", "snowflake", "bigquery", "clickhouse")
STATEMENT_KEYWORD_ALIASES = ("close", "delete", "print", "commit")


@pytest.mark.unit
@pytest.mark.parametrize("alias", STATEMENT_KEYWORD_ALIASES)
@pytest.mark.parametrize("dialect", NON_TSQL_ALIAS_DIALECTS)
def test_unquoted_statement_keyword_alias_is_accepted_outside_tsql(
    v, dialect: str, alias: str
) -> None:
    result = v.validate_sql(f"SELECT a AS {alias} FROM t", dialect)

    assert result.ok is True
    assert result.errors == ()
