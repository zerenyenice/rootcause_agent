"""Dimension tables: table set, counts and key columns.

Column-name contract (snake_case, implemented exactly as listed here):
  plants: plant_id          production_lines: line_id, plant_id
  machines: machine_id, line_id, machine_type, install_date
  product_families: family_id
  products: product_id, family_id, complexity_score, standard_cycle_time_s
  suppliers: supplier_id    materials: material_id
  material_lots: lot_id, material_id, supplier_id, received_date,
                 spec_measurement, certified
  operators: operator_id, hire_date, training_certified_date (may be null)
  shift_assignments: date, operator_id, line_id, shift
"""

from __future__ import annotations

import pytest

# pandas is added to packages/evals dependencies by the implementation task;
# tolerate its absence at import time so RED fails on the missing module, not collection.
try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover
    pd = None  # type: ignore[assignment]

EXPECTED_TABLES = {
    "plants",
    "production_lines",
    "machines",
    "product_families",
    "products",
    "suppliers",
    "materials",
    "material_lots",
    "operators",
    "shift_assignments",
    "work_orders",
    "production_runs",
    "quality_inspections",
    "machine_telemetry",
    "downtime_events",
    "maintenance_events",
    "gauge_calibrations",
    "energy_meter_readings",
    "warranty_claims",
    "business_events",
}

DIMENSION_COUNTS = {
    "plants": 2,
    "production_lines": 8,
    "machines": 40,
    "product_families": 5,
    "products": 30,
    "suppliers": 12,
    "materials": 20,
    "operators": 200,
}

DIMENSION_KEYS = {
    "plants": ["plant_id"],
    "production_lines": ["line_id", "plant_id"],
    "machines": ["machine_id", "line_id", "machine_type", "install_date"],
    "product_families": ["family_id"],
    "products": ["product_id", "family_id", "complexity_score", "standard_cycle_time_s"],
    "suppliers": ["supplier_id"],
    "materials": ["material_id"],
    "material_lots": [
        "lot_id",
        "material_id",
        "supplier_id",
        "received_date",
        "spec_measurement",
        "certified",
    ],
    "operators": ["operator_id", "hire_date"],
    "shift_assignments": ["date", "operator_id", "line_id", "shift"],
}


@pytest.mark.unit
def test_generate_returns_exactly_the_expected_tables(
    tables: dict[str, pd.DataFrame],
) -> None:
    assert set(tables) == EXPECTED_TABLES


@pytest.mark.unit
def test_every_table_is_a_dataframe(tables: dict[str, pd.DataFrame]) -> None:
    assert all(isinstance(frame, pd.DataFrame) for frame in tables.values())


@pytest.mark.unit
@pytest.mark.parametrize(("name", "count"), list(DIMENSION_COUNTS.items()))
def test_dimension_row_count_matches_defaults(
    tables: dict[str, pd.DataFrame], name: str, count: int
) -> None:
    assert len(tables[name]) == count


@pytest.mark.unit
@pytest.mark.parametrize(("name", "columns"), list(DIMENSION_KEYS.items()))
def test_dimension_key_columns_present_and_non_null(
    tables: dict[str, pd.DataFrame], name: str, columns: list[str]
) -> None:
    frame = tables[name]

    assert set(columns) <= set(frame.columns)
    assert not frame[columns].isna().any().any()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("plants", "plant_id"),
        ("production_lines", "line_id"),
        ("machines", "machine_id"),
        ("products", "product_id"),
        ("suppliers", "supplier_id"),
        ("operators", "operator_id"),
        ("material_lots", "lot_id"),
    ],
)
def test_primary_keys_are_unique(
    tables: dict[str, pd.DataFrame], name: str, key: str
) -> None:
    assert tables[name][key].is_unique


@pytest.mark.unit
def test_material_lots_scale_to_prd_volume(tables: dict[str, pd.DataFrame]) -> None:
    per_day = len(tables["material_lots"]) / 30
    expected = 2400 / 365

    assert expected * 0.5 <= per_day <= expected * 1.5


@pytest.mark.unit
def test_shift_assignments_cover_three_shifts_daily(
    tables: dict[str, pd.DataFrame],
) -> None:
    shifts = tables["shift_assignments"]

    assert set(shifts["shift"]) == {"day", "evening", "night"}
    assert pd.to_datetime(shifts["date"]).nunique() == 30


@pytest.mark.unit
def test_operator_training_certified_date_not_before_hire(
    tables: dict[str, pd.DataFrame],
) -> None:
    operators = tables["operators"].dropna(subset=["training_certified_date"])

    hired = pd.to_datetime(operators["hire_date"])
    certified = pd.to_datetime(operators["training_certified_date"])
    assert (certified >= hired).all()
