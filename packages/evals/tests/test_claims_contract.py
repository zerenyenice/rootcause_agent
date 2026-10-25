"""Warranty-claim contract: nullable run_id, lag, no clustering, dtypes, DDL FKs, dictionary."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover
    pd = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from pathlib import Path

SEED = 7
START = "2025-10-01"
YEAR_DAYS = 365
MIN_LAG_DAYS = 30
MAX_LAG_DAYS = 90
BURN_IN_DAYS = 90
MAX_NULL_RUN_SHARE = 0.35
MAX_CLAIMS_PER_RUN = 8
MAX_DAY_TO_MEDIAN = 4
SENTINELS = {"None", "nan", "<NA>"}

TEXT_NULLABLE = [
    ("business_events", "plant_id"),
    ("maintenance_events", "parts_replaced"),
    ("quality_inspections", "defect_type"),
    ("production_runs", "material_lot_id"),
]

REQUIRED_FKS = [
    ("products", "family_id"),
    ("products", "material_id"),
    ("operators", "home_line_id"),
    ("work_orders", "product_id"),
    ("production_runs", "product_id"),
    ("production_runs", "material_lot_id"),
    ("quality_inspections", "inspector_id"),
    ("downtime_events", "machine_id"),
    ("maintenance_events", "machine_id"),
    ("energy_meter_readings", "line_id"),
    ("business_events", "plant_id"),
    ("warranty_claims", "run_id"),
]


@pytest.fixture(scope="module")
def year_tables() -> dict[str, pd.DataFrame]:
    from rootcause_evals.manufacturing import GeneratorConfig, generate

    return generate(GeneratorConfig(seed=SEED, days=YEAR_DAYS))


def _claims_with_runs(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    runs = tables["production_runs"][["run_id", "ts"]]
    claims = tables["warranty_claims"].dropna(subset=["run_id"])
    return claims.merge(runs, on="run_id", how="inner")


@pytest.mark.unit
def test_claim_run_id_is_nullable_column(year_tables: dict[str, pd.DataFrame]) -> None:
    assert year_tables["warranty_claims"]["run_id"].isna().any()


@pytest.mark.unit
def test_every_non_null_claim_run_id_resolves(year_tables: dict[str, pd.DataFrame]) -> None:
    claims = year_tables["warranty_claims"].dropna(subset=["run_id"])

    assert len(claims) > 0
    assert claims["run_id"].isin(year_tables["production_runs"]["run_id"]).all()


@pytest.mark.unit
def test_claim_lag_is_between_30_and_90_days(year_tables: dict[str, pd.DataFrame]) -> None:
    joined = _claims_with_runs(year_tables)

    lag = (
        pd.to_datetime(joined["claim_date"]).dt.normalize()
        - pd.to_datetime(joined["ts"]).dt.normalize()
    ).dt.days

    assert len(joined) > 0
    assert lag.min() >= MIN_LAG_DAYS
    assert lag.max() <= MAX_LAG_DAYS


@pytest.mark.unit
def test_null_run_claims_fall_in_first_90_days(year_tables: dict[str, pd.DataFrame]) -> None:
    claims = year_tables["warranty_claims"]
    null_dates = pd.to_datetime(claims.loc[claims["run_id"].isna(), "claim_date"])
    cutoff = pd.Timestamp(START) + pd.Timedelta(days=BURN_IN_DAYS)

    assert len(null_dates) > 0
    assert (null_dates.dt.normalize() < cutoff).all()


@pytest.mark.unit
def test_no_null_run_claims_from_day_90_onward(year_tables: dict[str, pd.DataFrame]) -> None:
    claims = year_tables["warranty_claims"]
    cutoff = pd.Timestamp(START) + pd.Timedelta(days=BURN_IN_DAYS)
    late = claims[pd.to_datetime(claims["claim_date"]).dt.normalize() >= cutoff]

    assert len(late) > 0
    assert not late["run_id"].isna().any()


@pytest.mark.unit
def test_null_run_share_at_most_35_percent_of_claims(
    year_tables: dict[str, pd.DataFrame],
) -> None:
    assert year_tables["warranty_claims"]["run_id"].isna().mean() <= MAX_NULL_RUN_SHARE


@pytest.mark.unit
def test_no_run_has_more_than_8_claims(year_tables: dict[str, pd.DataFrame]) -> None:
    per_run = year_tables["warranty_claims"].dropna(subset=["run_id"]).groupby("run_id").size()

    assert per_run.max() <= MAX_CLAIMS_PER_RUN


@pytest.mark.unit
def test_no_production_day_clusters_claims(year_tables: dict[str, pd.DataFrame]) -> None:
    joined = _claims_with_runs(year_tables)
    per_day = joined.groupby(pd.to_datetime(joined["ts"]).dt.normalize()).size()

    assert len(per_day) > 0
    assert per_day.max() <= MAX_DAY_TO_MEDIAN * per_day.median()


@pytest.mark.unit
@pytest.mark.parametrize(("table", "column"), TEXT_NULLABLE)
def test_nullable_text_columns_share_dtype_with_plain_text(
    tables: dict[str, pd.DataFrame], table: str, column: str
) -> None:
    reference = tables["products"]["product_id"].dtype
    series = tables[table][column]

    assert series.dtype == reference
    assert series.isna().any()
    assert not series.map(lambda v: isinstance(v, str) and v in SENTINELS).any()


def _create_table_block(sql: str, table: str) -> str:
    match = re.search(rf"CREATE TABLE {table}\s*\((.*?)\n?\);", sql, re.DOTALL)
    assert match, f"no CREATE TABLE block for {table}"
    return match.group(1)


@pytest.mark.unit
@pytest.mark.parametrize(("table", "column"), REQUIRED_FKS)
def test_ddl_declares_foreign_key(
    tables: dict[str, pd.DataFrame], tmp_path: Path, table: str, column: str
) -> None:
    from rootcause_evals.manufacturing.writers import write_postgres_sql

    path = tmp_path / "warehouse.sql"
    write_postgres_sql(tables, path)
    block = _create_table_block(path.read_text(encoding="utf-8"), table)

    assert re.search(rf"FOREIGN KEY\s*\(\s*{column}\s*\)", block, re.IGNORECASE)


@pytest.mark.unit
def test_data_dictionary_documents_right_censoring(tmp_path: Path) -> None:
    from rootcause_evals.manufacturing.writers import write_data_dictionary

    write_data_dictionary(tmp_path)
    text = (tmp_path / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    section = text.split("## warranty_claims", 1)[1].split("\n## ", 1)[0].lower()

    assert "censored" in section or "window end" in section
