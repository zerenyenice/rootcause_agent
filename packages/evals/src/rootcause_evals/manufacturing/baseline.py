"""Baseline fact tables on the production grid: runs, work orders, telemetry, energy.

Per-run quantities are computed as ``(lines, periods)`` matrices and flattened
period-major, so ``production_runs`` is naturally ordered by timestamp then
line. All randomness comes from the generator passed in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from rootcause_evals.manufacturing.config import GeneratorConfig
from rootcause_evals.manufacturing.dimensions import MACHINE_TYPES
from rootcause_evals.manufacturing.events import build_event_tables
from rootcause_evals.manufacturing.seasonality import (
    MAINTENANCE_DIP_FACTOR,
    SHIFT_NAMES,
    Calendar,
    build_calendar,
)

SECONDS_PER_MINUTE = 60
BASE_AVAILABILITY = 0.85
OUTPUT_NOISE_SD = 0.08
OUTPUT_SHIFT_FACTOR = np.array([1.0, 0.97, 0.93])  # day, evening, night
BASE_SCRAP_RATE = 0.02
SCRAP_SHIFT_FACTOR = np.array([1.0, 1.15, 1.6])  # night-shift scrap penalty
MAX_SCRAP_RATE = 0.5
CYCLE_TIME_DRIFT = 1.02
CYCLE_TIME_NOISE_SD = 0.04
NIGHT_CYCLE_TIME_PENALTY = 0.02
MIN_WORK_ORDER_HOURS = 4
MAX_WORK_ORDER_HOURS = 12
FAMILIES_PER_LINE = 2
RECENT_LOTS_IN_USE = 3
MATERIAL_LOT_NULL_SHARE = 0.03
LINE_OVERHEAD_KWH = 12.0
ENERGY_NOISE_SD = 1.0
TELEMETRY_BY_TYPE: dict[str, tuple[float, float, float, float]] = {
    # machine_type: (base temperature C, base vibration mm/s, idle kW, kWh per unit)
    "press": (55.0, 3.5, 6.0, 0.25),
    "welder": (70.0, 2.5, 8.0, 0.35),
    "cnc_mill": (48.0, 4.5, 7.0, 0.30),
    "paint_robot": (40.0, 2.0, 5.0, 0.15),
    "assembly_cell": (35.0, 1.5, 3.0, 0.10),
}
DIURNAL_TEMPERATURE_SWING_C = 2.0
TEMPERATURE_PER_UNIT_C = 0.08
VIBRATION_PER_UNIT = 0.004
VIBRATION_PER_YEAR = 0.1
PRESSURE_BAR = 6.2
DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class RunGrid:
    """Intermediate ``(lines, periods)`` matrices for the production grid."""

    work_order_idx: npt.NDArray[np.int64]
    product_idx: npt.NDArray[np.int64]
    units_produced: npt.NDArray[np.int64]
    units_scrapped: npt.NDArray[np.int64]
    cycle_time_s: npt.NDArray[np.float64]
    work_order_periods: npt.NDArray[np.int64]  # duration per work order (1-D)
    work_order_product: npt.NDArray[np.int64]  # product index per work order (1-D)


def _flat[S: np.generic](matrix: npt.NDArray[S]) -> npt.NDArray[S]:
    """Flatten a ``(lines, periods)`` matrix period-major."""
    return np.ascontiguousarray(matrix.T).reshape(-1)


def _work_orders(
    config: GeneratorConfig, rng: np.random.Generator, products: pd.DataFrame
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """Segment each line's periods into work orders of 4-12 hours.

    Returns the ``(lines, periods)`` global work order index, the duration in
    periods of each work order and the product index each work order builds.
    """
    per_hour = config.periods_per_hour
    family_of_product = (np.arange(config.products) % config.families).astype(np.int64)
    indices, durations, chosen = [], [], []
    offset = 0
    for line in range(config.lines):
        max_orders = config.periods // (MIN_WORK_ORDER_HOURS * per_hour) + 1
        drawn = rng.integers(MIN_WORK_ORDER_HOURS, MAX_WORK_ORDER_HOURS + 1, size=max_orders)
        drawn = drawn * per_hour
        local = np.repeat(np.arange(max_orders), drawn)[: config.periods]
        used = int(local[-1]) + 1
        counts = np.bincount(local, minlength=used)[:used]
        families = (line + np.arange(FAMILIES_PER_LINE)) % config.families
        pool = np.flatnonzero(np.isin(family_of_product, families))
        indices.append(local + offset)
        durations.append(counts)
        chosen.append(pool[rng.integers(0, len(pool), size=used)])
        offset += used
    return (
        np.stack(indices).astype(np.int64),
        np.concatenate(durations).astype(np.int64),
        np.concatenate(chosen).astype(np.int64),
    )


def build_run_grid(
    config: GeneratorConfig, rng: np.random.Generator, calendar: Calendar, products: pd.DataFrame
) -> RunGrid:
    """Draw units produced, scrapped and cycle times for every line-period."""
    work_order_idx, wo_periods, wo_product = _work_orders(config, rng, products)
    product_idx = wo_product[work_order_idx]
    std_cycle = products["standard_cycle_time_s"].to_numpy(dtype=float)[product_idx]
    complexity = products["complexity_score"].to_numpy(dtype=float)[product_idx]
    shift_idx = np.searchsorted(np.array(SHIFT_NAMES), calendar.shift)[None, :]
    capacity = config.grain_minutes * SECONDS_PER_MINUTE / std_cycle
    dip = np.where(calendar.maintenance_dip, MAINTENANCE_DIP_FACTOR, 1.0)
    noise = np.clip(1.0 + rng.normal(0.0, OUTPUT_NOISE_SD, size=capacity.shape), 0.5, 1.5)
    expected = (
        capacity
        * BASE_AVAILABILITY
        * OUTPUT_SHIFT_FACTOR[shift_idx]
        * calendar.output_factor[None, :]
        * dip
        * noise
    )
    units = rng.poisson(expected).astype(np.int64)
    scrap_rate = BASE_SCRAP_RATE * SCRAP_SHIFT_FACTOR[shift_idx] * (0.8 + 0.1 * complexity)
    scrapped = rng.binomial(units, np.minimum(scrap_rate, MAX_SCRAP_RATE)).astype(np.int64)
    is_night = shift_idx == SHIFT_NAMES.index("night")
    cycle = std_cycle * (CYCLE_TIME_DRIFT + CYCLE_TIME_NOISE_SD * rng.normal(size=std_cycle.shape))
    cycle = cycle * (1.0 + NIGHT_CYCLE_TIME_PENALTY * is_night)
    return RunGrid(
        work_order_idx=work_order_idx,
        product_idx=product_idx,
        units_produced=units,
        units_scrapped=scrapped,
        cycle_time_s=np.round(np.maximum(cycle, 0.9 * std_cycle), 2),
        work_order_periods=wo_periods,
        work_order_product=wo_product,
    )


def assign_material_lots(
    rng: np.random.Generator,
    run_ts: npt.NDArray[np.datetime64],
    run_material: npt.NDArray[np.str_],
    lots: pd.DataFrame,
) -> pd.Series:
    """Pick one of the most recently received lots of the run's material.

    Lots are sorted by ``received_date``; the opening-stock lot per material
    guarantees at least one eligible lot. Exactly ``MATERIAL_LOT_NULL_SHARE``
    of runs are then blanked (PRD data trap).
    """
    chosen = np.empty(len(run_ts), dtype=object)
    received_all = lots["received_date"].to_numpy()
    lot_ids_all = lots["lot_id"].to_numpy()
    material_all = lots["material_id"].to_numpy()
    for material in pd.unique(run_material):
        lot_mask = material_all == material
        received = received_all[lot_mask]
        run_mask = run_material == material
        eligible = np.searchsorted(received, run_ts[run_mask], side="right")
        back = rng.integers(0, RECENT_LOTS_IN_USE, size=int(run_mask.sum()))
        chosen[run_mask] = lot_ids_all[lot_mask][np.clip(eligible - 1 - back, 0, None)]
    blank = rng.choice(len(run_ts), size=int(MATERIAL_LOT_NULL_SHARE * len(run_ts)), replace=False)
    chosen[blank] = None
    return pd.Series(chosen, dtype="str")


def build_production_runs(
    config: GeneratorConfig,
    rng: np.random.Generator,
    calendar: Calendar,
    grid: RunGrid,
    dims: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    products = dims["products"]
    line_ids = dims["production_lines"]["line_id"].to_numpy()
    n = config.lines * config.periods
    ts = np.repeat(calendar.ts, config.lines)
    product_idx = _flat(grid.product_idx)
    material = products["material_id"].to_numpy()[product_idx]
    return pd.DataFrame(
        {
            "run_id": np.arange(1, n + 1, dtype=np.int64),
            "ts": ts,
            "line_id": np.tile(line_ids, config.periods),
            "shift": np.repeat(calendar.shift, config.lines),
            "product_id": products["product_id"].to_numpy()[product_idx],
            "work_order_id": _flat(grid.work_order_idx) + 1,
            "units_produced": _flat(grid.units_produced),
            "units_scrapped": _flat(grid.units_scrapped),
            "cycle_time_s": _flat(grid.cycle_time_s),
            "material_lot_id": assign_material_lots(rng, ts, material, dims["material_lots"]),
        }
    )


def build_work_orders(
    config: GeneratorConfig, grid: RunGrid, runs: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    span = runs.groupby("work_order_id", sort=True)["ts"].agg(["min", "max"])
    line = runs.groupby("work_order_id", sort=True)["line_id"].first()
    std_cycle = products["standard_cycle_time_s"].to_numpy(dtype=float)[grid.work_order_product]
    hours = grid.work_order_periods / config.periods_per_hour
    planned = np.round(hours * 3600.0 / std_cycle * BASE_AVAILABILITY / 10.0) * 10
    return pd.DataFrame(
        {
            "work_order_id": span.index.to_numpy(),
            "product_id": products["product_id"].to_numpy()[grid.work_order_product],
            "line_id": line.to_numpy(),
            "planned_qty": planned.astype(np.int64),
            "start_ts": span["min"].to_numpy(),
            "end_ts": span["max"].to_numpy() + np.timedelta64(config.grain_minutes, "m"),
            "status": "closed",
        }
    )


def build_machine_telemetry(
    config: GeneratorConfig,
    rng: np.random.Generator,
    calendar: Calendar,
    grid: RunGrid,
    machines: pd.DataFrame,
) -> tuple[pd.DataFrame, npt.NDArray[np.float64]]:
    """Hourly sensor readings per machine; also returns the ``(machines, periods)`` power."""
    params = np.array([TELEMETRY_BY_TYPE[t] for t in MACHINE_TYPES])
    type_idx = np.array([MACHINE_TYPES.index(t) for t in machines["machine_type"]])
    base_temp, base_vib, idle_kw, kw_per_unit = (params[type_idx][:, i][:, None] for i in range(4))
    age_days = (pd.Timestamp(config.start) - machines["install_date"]).dt.days
    age_years = age_days.to_numpy(dtype=float)[:, None] / DAYS_PER_YEAR
    units = np.repeat(grid.units_produced, config.machines_per_line, axis=0)
    shape = units.shape
    diurnal = DIURNAL_TEMPERATURE_SWING_C * np.sin(2 * np.pi * (calendar.hour - 9) / 24)[None, :]
    temperature = base_temp + TEMPERATURE_PER_UNIT_C * units + diurnal + rng.normal(0, 1.2, shape)
    vibration = base_vib + VIBRATION_PER_UNIT * units + VIBRATION_PER_YEAR * age_years
    vibration = vibration + rng.normal(0, 0.3, shape)
    pressure = PRESSURE_BAR + rng.normal(0, 0.15, shape)
    power = np.maximum(idle_kw + kw_per_unit * units + rng.normal(0, 0.5, shape), 0.0)
    frame = pd.DataFrame(
        {
            "ts": np.repeat(calendar.ts, config.machines),
            "machine_id": np.tile(machines["machine_id"].to_numpy(), config.periods),
            "temperature_c": np.round(_flat(temperature), 2),
            "vibration_mm_s": np.round(_flat(np.maximum(vibration, 0.0)), 3),
            "pressure_bar": np.round(_flat(pressure), 3),
            "power_kwh": np.round(_flat(power), 3),
        }
    )
    return frame, np.asarray(power, dtype=np.float64)


def build_energy_meter_readings(
    config: GeneratorConfig,
    rng: np.random.Generator,
    calendar: Calendar,
    machine_power: npt.NDArray[np.float64],
    lines: pd.DataFrame,
) -> pd.DataFrame:
    per_line = machine_power.reshape(config.lines, config.machines_per_line, config.periods).sum(1)
    kwh = per_line + LINE_OVERHEAD_KWH + rng.normal(0.0, ENERGY_NOISE_SD, size=per_line.shape)
    return pd.DataFrame(
        {
            "ts": np.repeat(calendar.ts, config.lines),
            "line_id": np.tile(lines["line_id"].to_numpy(), config.periods),
            "kwh": np.round(_flat(np.maximum(kwh, 0.0)), 3),
        }
    )


def build_baseline(
    config: GeneratorConfig, rng: np.random.Generator, dims: dict[str, pd.DataFrame]
) -> dict[str, pd.DataFrame]:
    """Build every fact table from the dimensions, in a fixed order."""
    calendar = build_calendar(config)
    grid = build_run_grid(config, rng, calendar, dims["products"])
    runs = build_production_runs(config, rng, calendar, grid, dims)
    work_orders = build_work_orders(config, grid, runs, dims["products"])
    telemetry, power = build_machine_telemetry(config, rng, calendar, grid, dims["machines"])
    energy = build_energy_meter_readings(config, rng, calendar, power, dims["production_lines"])
    events = build_event_tables(config, rng, calendar, runs, dims)
    return {
        "work_orders": work_orders,
        "production_runs": runs,
        "quality_inspections": events["quality_inspections"],
        "machine_telemetry": telemetry,
        "downtime_events": events["downtime_events"],
        "maintenance_events": events["maintenance_events"],
        "gauge_calibrations": events["gauge_calibrations"],
        "energy_meter_readings": energy,
        "warranty_claims": events["warranty_claims"],
        "business_events": events["business_events"],
    }
