"""Safe analytic DuckDB SQL must not be rejected; the real hazards must still be."""

from __future__ import annotations

import duckdb
import pytest

from rootcause.connections.duckdb_executor import load_function_catalog
from rootcause.connections.validation import CatalogSnapshot, validate_sql

CATALOG = CatalogSnapshot(
    {
        "downtime_events": ("event_id", "machine_id", "start_ts", "end_ts", "duration_min"),
        "machines": ("machine_id", "line_id", "machine_type"),
    }
)

FUNCTIONS = load_function_catalog(duckdb.connect())

SAFE_FUNCTIONS = [
    "date_sub('day', DATE '2024-01-01', start_ts)",
    "datesub('minute', start_ts, end_ts)",
    "age(end_ts, start_ts)",
    "epoch_ms(start_ts)",
    "isoyear(start_ts)",
    "weekofyear(start_ts)",
    "make_timestamp(2024, 1, 1, 0, 0, 0)",
    "time_bucket(INTERVAL '1 day', start_ts)",
    "regr_slope(duration_min, duration_min)",
    "regr_r2(duration_min, duration_min)",
    "entropy(machine_id)",
    "kurtosis(duration_min)",
    "mad(duration_min)",
    "histogram(machine_id)",
    "arbitrary(machine_id)",
    "mean(duration_min)",
    "product(duration_min)",
    "max_by(machine_id, duration_min)",
    "list_sort(list(duration_min))",
    "json_extract_string('{}', '$.a')",
    "to_json(duration_min)",
    "hash(machine_id)",
    "jaro_winkler_similarity(machine_id, 'M001')",
    "format_bytes(duration_min)",
    "weekday(start_ts)",
    "to_minutes(duration_min)",
    "try_strptime('2024-01-01', '%Y-%m-%d')",
]


@pytest.mark.unit
@pytest.mark.parametrize("call", SAFE_FUNCTIONS)
def test_pure_duckdb_function_is_not_rejected(call: str) -> None:
    result = validate_sql(
        f"SELECT {call} FROM downtime_events", "duckdb", catalog=CATALOG, functions=FUNCTIONS
    )

    assert result.ok, [issue.message for issue in result.errors]


SAFE_QUERIES = [
    # lateral column alias in the select list
    "SELECT duration_min / 60.0 AS hrs, hrs * 2 AS dbl FROM downtime_events",
    # select alias in WHERE
    "SELECT machine_id AS m FROM downtime_events WHERE m <> 'M001'",
    "SELECT * FROM downtime_events WHERE machine_id = 'M001' AND duration_min > 0",
    "SELECT sum(duration_min) AS total, total / 60 AS hours FROM downtime_events",
    "SELECT date_trunc('day', start_ts) AS d, count(*) FROM downtime_events GROUP BY d",
    "SELECT machine_id FROM downtime_events GROUP BY ALL QUALIFY row_number() OVER () < 3",
    "SELECT DISTINCT ON (machine_id) machine_id, start_ts FROM downtime_events "
    "ORDER BY machine_id, start_ts DESC",
]


@pytest.mark.unit
@pytest.mark.parametrize("sql", SAFE_QUERIES)
def test_safe_analytic_query_is_not_rejected(sql: str) -> None:
    result = validate_sql(sql, "duckdb", catalog=CATALOG, functions=FUNCTIONS)

    assert result.ok, [issue.message for issue in result.errors]


@pytest.mark.unit
def test_self_aliased_unknown_column_is_still_unknown() -> None:
    result = validate_sql(
        "SELECT nope AS nope FROM downtime_events", "duckdb", catalog=CATALOG, functions=FUNCTIONS
    )

    assert not result.ok
    assert [issue.code.value for issue in result.errors] == ["unknown_column"]


HAZARDOUS_CALLS = [
    "read_csv('/etc/passwd')",
    "read_text('/etc/passwd')",
    "parquet_scan('x.parquet')",
    "glob('/*')",
    "getenv('HOME')",
    "current_setting('home_directory')",
    "getvariable('x')",
    "query('SELECT 1')",
    "query_table('machines')",
    "nextval('s')",
    "setval('s', 1)",
    "sleep_ms(10)",
    "write_log('x')",
    "duckdb_settings()",
    "pragma_database_list()",
    "sqlite_scan('x.db', 't')",
    "pg_sleep(1)",
    "main.some_macro(1)",
    "COLUMNS('.*')",
    "generate_subscripts([1, 2], 1)",
    "get_block_size('x')",
    "current_query()",
    "current_database()",
    "version()",
    "in_search_path('a', 'b')",
    "__internal_barrier(1)",
    "totally_unknown_function(1)",
]


@pytest.mark.unit
@pytest.mark.parametrize("call", HAZARDOUS_CALLS)
def test_file_system_and_state_functions_are_still_rejected(call: str) -> None:
    for sql in (f"SELECT {call}", f"SELECT * FROM {call}"):
        result = validate_sql(sql, "duckdb", catalog=CATALOG, functions=FUNCTIONS)

        assert not result.ok, sql


@pytest.mark.unit
def test_strict_allow_list_is_unchanged_for_other_dialects() -> None:
    result = validate_sql("SELECT regr_slope(a, b) FROM t", "postgres")

    assert not result.ok
    assert result.errors[0].code.value == "forbidden_function"


@pytest.mark.unit
def test_unknown_column_message_lists_the_table_columns() -> None:
    result = validate_sql(
        "SELECT d.nope FROM downtime_events d", "duckdb", catalog=CATALOG, functions=FUNCTIONS
    )

    message = result.errors[0].message
    assert "nope" in message
    assert "columns are:" in message
    assert "duration_min" in message and "machine_id" in message


@pytest.mark.unit
def test_forbidden_function_message_names_the_function() -> None:
    result = validate_sql("SELECT getenv('HOME')", "duckdb", catalog=CATALOG, functions=FUNCTIONS)

    assert "getenv" in result.errors[0].message


COLUMN_WINS_CATALOG = CatalogSnapshot({"t1": ("id", "x")})


@pytest.mark.unit
def test_source_column_wins_over_a_select_alias_of_the_same_name() -> None:
    # DuckDB filters on t1.x here, not on the alias; both bind, so this must validate.
    result = validate_sql(
        "SELECT t1.id AS x FROM t1 WHERE x = 2", "duckdb", catalog=COLUMN_WINS_CATALOG
    )

    assert result.ok


@pytest.mark.unit
def test_alias_is_used_only_when_no_source_has_the_column() -> None:
    ok = validate_sql("SELECT id AS y FROM t1 WHERE y = 2", "duckdb", catalog=COLUMN_WINS_CATALOG)
    bad = validate_sql("SELECT id FROM t1 WHERE y = 2", "duckdb", catalog=COLUMN_WINS_CATALOG)

    assert ok.ok
    assert [issue.code.value for issue in bad.errors] == ["unknown_column"]


@pytest.mark.unit
def test_ambiguous_alias_and_column_is_not_reported_as_unknown() -> None:
    result = validate_sql(
        "SELECT id AS x, x + 1 AS y FROM t1", "duckdb", catalog=COLUMN_WINS_CATALOG
    )

    assert result.ok


@pytest.mark.unit
def test_without_a_function_catalog_duckdb_keeps_the_strict_allow_list() -> None:
    result = validate_sql("SELECT date_sub('day', DATE '2024-01-01', DATE '2024-01-02')", "duckdb")

    assert not result.ok
