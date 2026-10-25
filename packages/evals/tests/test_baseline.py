"""Baseline fact tables: schema, rates, integrity, behaviour, determinism.

Column-name contract (snake_case, implemented exactly as listed here):
  production_runs: run_id, ts, line_id, shift, product_id, work_order_id,
      units_produced, units_scrapped, cycle_time_s, material_lot_id (nullable <= 3%)
  work_orders: work_order_id, product_id, line_id, planned_qty, start_ts, end_ts
  quality_inspections: inspection_id, run_id, inspector_id, gauge_id,
      measured_value, passed (bool), defect_type (nullable), source_system ("MES")
  machine_telemetry: ts, machine_id, temperature_c, vibration_mm_s, pressure_bar, power_kwh
  downtime_events: event_id, machine_id, start_ts, end_ts, reason_code
  maintenance_events: event_id, machine_id, kind (planned|unplanned), scheduled_date,
      performed_date (nullable), parts_replaced (nullable)
  gauge_calibrations: calibration_id, gauge_id, due_date, performed_date, offset_found
  energy_meter_readings: ts, line_id, kwh
  warranty_claims: claim_id, product_id, run_id, claim_date, defect_type
  business_events: event_date, event_type, description
"""

from __future__ import annotations

import pytest

# pandas is added to packages/evals dependencies by the implementation task;
# tolerate its absence at import time so RED fails on the missing module, not collection.
try:
    import pandas as pd
    import pandas.testing as pdt
except ModuleNotFoundError:  # pragma: no cover
    pd = pdt = None  # type: ignore[assignment]

DAYS = 30
LINES = 8
MACHINES = 40

NON_NULL_KEYS = {
    "production_runs": [
        "run_id",
        "ts",
        "line_id",
        "shift",
        "product_id",
        "work_order_id",
        "units_produced",
        "units_scrapped",
        "cycle_time_s",
    ],
    "work_orders": [
        "work_order_id",
        "product_id",
        "line_id",
        "planned_qty",
        "start_ts",
        "end_ts",
    ],
    "quality_inspections": [
        "inspection_id",
        "run_id",
        "inspector_id",
        "gauge_id",
        "measured_value",
        "passed",
        "source_system",
    ],
    "machine_telemetry": [
        "ts",
        "machine_id",
        "temperature_c",
        "vibration_mm_s",
        "pressure_bar",
        "power_kwh",
    ],
    "downtime_events": ["event_id", "machine_id", "start_ts", "end_ts", "reason_code"],
    "maintenance_events": ["event_id", "machine_id", "kind", "scheduled_date"],
    "gauge_calibrations": [
        "calibration_id",
        "gauge_id",
        "due_date",
        "performed_date",
        "offset_found",
    ],
    "energy_meter_readings": ["ts", "line_id", "kwh"],
    "warranty_claims": ["claim_id", "product_id", "claim_date", "defect_type"],
    "business_events": ["event_date", "event_type", "description"],
}

PRESENT_ONLY = {
    "production_runs": ["material_lot_id"],
    "quality_inspections": ["defect_type"],
    "maintenance_events": ["performed_date", "parts_replaced"],
}


def _within(count: int, per_year: float, tolerance: float) -> bool:
    expected = per_year / 365 * DAYS
    return expected * (1 - tolerance) <= count <= expected * (1 + tolerance)


@pytest.mark.unit
@pytest.mark.parametrize(("name", "columns"), list(NON_NULL_KEYS.items()))
def test_key_columns_present_and_non_null(
    tables: dict[str, pd.DataFrame], name: str, columns: list[str]
) -> None:
    frame = tables[name]

    assert len(frame) > 0
    assert set(columns) <= set(frame.columns)
    assert not frame[columns].isna().any().any()


@pytest.mark.unit
@pytest.mark.parametrize(("name", "columns"), list(PRESENT_ONLY.items()))
def test_nullable_columns_exist(
    tables: dict[str, pd.DataFrame], name: str, columns: list[str]
) -> None:
    assert set(columns) <= set(tables[name].columns)


@pytest.mark.unit
def test_material_lot_null_share_at_most_three_percent(
    tables: dict[str, pd.DataFrame],
) -> None:
    assert tables["production_runs"]["material_lot_id"].isna().mean() <= 0.03


@pytest.mark.unit
def test_production_runs_hourly_per_line(tables: dict[str, pd.DataFrame]) -> None:
    assert len(tables["production_runs"]) == LINES * 24 * DAYS


@pytest.mark.unit
def test_machine_telemetry_hourly_per_machine(tables: dict[str, pd.DataFrame]) -> None:
    assert len(tables["machine_telemetry"]) == MACHINES * 24 * DAYS


@pytest.mark.unit
def test_energy_readings_hourly_per_line(tables: dict[str, pd.DataFrame]) -> None:
    assert len(tables["energy_meter_readings"]) == LINES * 24 * DAYS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "per_year", "tolerance"),
    [
        ("work_orders", 9000, 0.25),
        ("quality_inspections", 90000, 0.25),
        ("downtime_events", 25000, 0.40),
    ],
)
def test_event_rate_matches_prd_volume(
    tables: dict[str, pd.DataFrame], name: str, per_year: float, tolerance: float
) -> None:
    assert _within(len(tables[name]), per_year, tolerance)


@pytest.mark.unit
def test_warranty_claims_sanity_in_30_day_window(tables: dict[str, pd.DataFrame]) -> None:
    claims = tables["warranty_claims"]
    runs = tables["production_runs"]
    window_start = pd.to_datetime(runs["ts"]).min().normalize()
    window_end = window_start + pd.Timedelta(days=DAYS)
    claim_dates = pd.to_datetime(claims["claim_date"])

    assert len(claims) > 0
    assert ((claim_dates >= window_start) & (claim_dates < window_end)).all()
    assert claims["run_id"].dropna().isin(runs["run_id"]).all()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("child", "column", "parent", "parent_column"),
    [
        ("production_runs", "line_id", "production_lines", "line_id"),
        ("production_runs", "work_order_id", "work_orders", "work_order_id"),
        ("quality_inspections", "run_id", "production_runs", "run_id"),
        ("machine_telemetry", "machine_id", "machines", "machine_id"),
        ("warranty_claims", "run_id", "production_runs", "run_id"),
        ("shift_assignments", "operator_id", "operators", "operator_id"),
        ("material_lots", "supplier_id", "suppliers", "supplier_id"),
    ],
)
def test_foreign_keys_resolve(
    tables: dict[str, pd.DataFrame],
    child: str,
    column: str,
    parent: str,
    parent_column: str,
) -> None:
    values = tables[child][column].dropna()  # nullable FKs (burn-in claims) are exempt
    orphans = ~values.isin(tables[parent][parent_column])

    assert not orphans.any()


@pytest.mark.unit
def test_weekend_output_below_eighty_percent_of_weekday(
    tables: dict[str, pd.DataFrame],
) -> None:
    runs = tables["production_runs"]
    dow = pd.to_datetime(runs["ts"]).dt.day_of_week
    weekend = runs.loc[dow >= 5, "units_produced"].mean()
    weekday = runs.loc[dow < 5, "units_produced"].mean()

    assert weekend < 0.8 * weekday


@pytest.mark.unit
def test_night_shift_scrap_rate_exceeds_day_shift(
    tables: dict[str, pd.DataFrame],
) -> None:
    runs = tables["production_runs"]
    totals = runs.groupby("shift")[["units_scrapped", "units_produced"]].sum()
    rate = totals["units_scrapped"] / totals["units_produced"]

    assert rate["night"] > rate["day"]


@pytest.mark.unit
def test_downtime_reason_codes_are_in_allowed_set(
    tables: dict[str, pd.DataFrame],
) -> None:
    allowed = {"breakdown", "changeover", "minor_stop", "power", "planned"}

    assert set(tables["downtime_events"]["reason_code"]) <= allowed


@pytest.mark.unit
def test_downtime_ends_after_it_starts(tables: dict[str, pd.DataFrame]) -> None:
    events = tables["downtime_events"]

    assert (pd.to_datetime(events["end_ts"]) > pd.to_datetime(events["start_ts"])).all()


@pytest.mark.unit
def test_shift_values_are_in_allowed_set(tables: dict[str, pd.DataFrame]) -> None:
    allowed = {"day", "evening", "night"}

    assert set(tables["production_runs"]["shift"]) <= allowed


@pytest.mark.unit
def test_units_scrapped_never_exceed_units_produced(
    tables: dict[str, pd.DataFrame],
) -> None:
    runs = tables["production_runs"]

    assert (runs["units_scrapped"] >= 0).all()
    assert (runs["units_scrapped"] <= runs["units_produced"]).all()


@pytest.mark.unit
def test_inspection_source_system_is_mes_in_baseline(
    tables: dict[str, pd.DataFrame],
) -> None:
    assert set(tables["quality_inspections"]["source_system"]) == {"MES"}


@pytest.mark.unit
def test_maintenance_kind_is_planned_or_unplanned(
    tables: dict[str, pd.DataFrame],
) -> None:
    assert set(tables["maintenance_events"]["kind"]) <= {"planned", "unplanned"}


@pytest.mark.unit
def test_same_config_generates_identical_frames() -> None:
    from rootcause_evals.manufacturing import GeneratorConfig, generate

    config = GeneratorConfig(seed=11, days=5)

    first = generate(config)
    second = generate(config)

    assert first.keys() == second.keys()
    for name in first:
        pdt.assert_frame_equal(first[name], second[name])


@pytest.mark.unit
def test_different_seed_changes_production_runs(
    tables: dict[str, pd.DataFrame],
) -> None:
    from rootcause_evals.manufacturing import GeneratorConfig, generate

    other = generate(GeneratorConfig(seed=8, days=30))

    with pytest.raises(AssertionError):
        pdt.assert_frame_equal(tables["production_runs"], other["production_runs"])


@pytest.mark.integration
def test_full_year_generation_under_two_minutes() -> None:
    import time

    from rootcause_evals.manufacturing import GeneratorConfig, generate

    started = time.perf_counter()
    result = generate(GeneratorConfig())
    elapsed = time.perf_counter() - started

    assert len(result["production_runs"]) == LINES * 24 * 365
    assert elapsed < 120
