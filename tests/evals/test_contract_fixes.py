"""Contract pins: real nulls, claim/maintenance dates in window, install dates, SQL dump."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover
    pd = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from pathlib import Path

DAYS = 30
SEED = 7
START = "2025-10-01"
LONG_DAYS = 180
SENTINELS = {"None", "nan", "NaT"}
NULLABLE_COLUMNS = [
    ("business_events", "plant_id"),
    ("maintenance_events", "parts_replaced"),
    ("operators", "training_certified_date"),
    ("quality_inspections", "defect_type"),
    ("production_runs", "material_lot_id"),
]


def _window_end(days: int) -> pd.Timestamp:
    """Last instant of the final window day."""
    return pd.Timestamp(START) + pd.Timedelta(days=days - 1, hours=23, minutes=59)


@pytest.fixture(scope="module")
def long_tables() -> dict[str, pd.DataFrame]:
    from rootcause.evals.manufacturing import GeneratorConfig, generate

    return generate(GeneratorConfig(seed=SEED, days=LONG_DAYS))


@pytest.mark.unit
@pytest.mark.parametrize(("table", "column"), NULLABLE_COLUMNS)
def test_nulls_are_real_nulls_not_sentinel_strings(
    tables: dict[str, pd.DataFrame], table: str, column: str
) -> None:
    series = tables[table][column]

    sentinel_hits = series.map(lambda v: isinstance(v, str) and v in SENTINELS)

    assert not sentinel_hits.any()
    assert series.isna().any()


@pytest.mark.unit
def test_warranty_claims_not_dated_after_window_end(
    tables: dict[str, pd.DataFrame],
) -> None:
    dates = pd.to_datetime(tables["warranty_claims"]["claim_date"])

    assert len(dates) > 0
    assert (dates <= _window_end(DAYS)).all()


@pytest.mark.unit
def test_warranty_claims_early_window_rate_at_least_forty_percent_of_steady_state(
    long_tables: dict[str, pd.DataFrame],
) -> None:
    start = pd.Timestamp(START)
    dates = pd.to_datetime(long_tables["warranty_claims"]["claim_date"])
    offsets = (dates.dt.normalize() - start).dt.days
    early = int((offsets < 30).sum()) / 30
    steady = int(((offsets >= 120) & (offsets < LONG_DAYS)).sum()) / (LONG_DAYS - 120)

    assert steady > 0
    assert early > 0
    assert early >= 0.4 * steady


@pytest.mark.unit
def test_warranty_claims_all_within_long_window(
    long_tables: dict[str, pd.DataFrame],
) -> None:
    dates = pd.to_datetime(long_tables["warranty_claims"]["claim_date"])

    assert (dates <= _window_end(LONG_DAYS)).all()


@pytest.mark.unit
def test_warranty_claim_rate_stays_within_prd_tolerance(
    tables: dict[str, pd.DataFrame],
) -> None:
    expected = 6000 / 365 * DAYS
    count = len(tables["warranty_claims"])

    assert expected * 0.5 <= count <= expected * 1.5


@pytest.mark.unit
def test_machine_install_dates_precede_window_start(
    tables: dict[str, pd.DataFrame],
) -> None:
    installs = pd.to_datetime(tables["machines"]["install_date"])

    assert (installs < pd.Timestamp(START)).all()


@pytest.mark.unit
def test_maintenance_performed_dates_not_after_window_end(
    tables: dict[str, pd.DataFrame],
) -> None:
    performed = pd.to_datetime(tables["maintenance_events"]["performed_date"]).dropna()

    assert len(performed) > 0
    assert (performed <= _window_end(DAYS)).all()


@pytest.mark.unit
def test_postgres_writer_escapes_carriage_return(tmp_path: Path) -> None:
    from rootcause.evals.manufacturing.writers import write_postgres_sql

    frame = pd.DataFrame({"id": [1, 2], "note": ["line1\rline2", "plain"]})
    path = tmp_path / "cr.sql"

    write_postgres_sql({"notes": frame}, path)
    raw = path.read_bytes()

    assert b"\\r" in raw
    assert b"\r" not in raw


@pytest.mark.unit
@pytest.mark.parametrize(
    ("table", "key"),
    [
        ("shift_assignments", "date, operator_id"),
        ("machine_telemetry", "ts, machine_id"),
        ("energy_meter_readings", "ts, line_id"),
    ],
)
def test_postgres_ddl_declares_composite_primary_key(
    tables: dict[str, pd.DataFrame], tmp_path: Path, table: str, key: str
) -> None:
    from rootcause.evals.manufacturing.writers import write_postgres_sql

    path = tmp_path / "warehouse.sql"
    write_postgres_sql(tables, path)
    text = path.read_text(encoding="utf-8")
    block = text.split(f"CREATE TABLE {table}", 1)[1].split(");", 1)[0]

    assert "PRIMARY KEY (" in block
    assert f"PRIMARY KEY ({key})" in block
