"""Event-grain fact tables derived from the production grid.

Inspections, downtime, maintenance, gauge calibrations, warranty claims and
business events. Everything is vectorised; the only Python loops run over
machines-per-line-sized or month-sized collections.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd

from rootcause_evals.manufacturing.config import GeneratorConfig
from rootcause_evals.manufacturing.dimensions import make_ids
from rootcause_evals.manufacturing.seasonality import Calendar

SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
INSPECTION_EXTRA_TRIALS = 2
INSPECTION_EXTRA_PROBABILITY = 0.15
GAUGES_PER_LINE = 12
MEASUREMENT_SD_IN_TOLERANCES = 1 / 2.2
SOURCE_SYSTEM = "MES"
DEFECT_TYPES: tuple[str, ...] = ("dimension", "surface", "crack", "porosity", "assembly")
WARRANTY_DEFECT_TYPES: tuple[str, ...] = ("noise", "leak", "electrical", "cosmetic", "failure")
DOWNTIME_PER_MACHINE_HOUR = 25_000 / (40 * 8_760)
DOWNTIME_REASONS: tuple[str, ...] = ("minor_stop", "changeover", "breakdown", "planned", "power")
DOWNTIME_REASON_P = np.array([0.50, 0.30, 0.12, 0.07, 0.01])
DOWNTIME_MINUTES_LOW = np.array([2.0, 10.0, 30.0, 60.0, 30.0])
DOWNTIME_MINUTES_HIGH = np.array([10.0, 30.0, 240.0, 240.0, 120.0])
PLANNED_PERFORMED_LAG_DAYS = np.array([0, 0, 0, 1, 2])
PLANNED_SKIPPED_SHARE = 0.03
UNPLANNED_SHARE_OF_BREAKDOWNS = 0.45
PLANNED_PARTS = np.array([None, None, "filter", "belt", "seal", "lubricant"], dtype=object)
UNPLANNED_PARTS = np.array(["bearing", "motor", "sensor", "pump", "gearbox"], dtype=object)
CALIBRATION_INTERVAL_DAYS = 91
CALIBRATION_LAG_DAYS = np.array([-2, -1, 0, 0, 0, 1, 3])
CALIBRATION_OFFSET_SD = 0.015
CLAIMS_PER_UNIT = 0.0018
CLAIM_LAG_MIN_DAYS = 30
CLAIM_LAG_MAX_DAYS = 90
CLAIM_BURN_IN_DAYS = 90
MONTHLY_MAINTENANCE_EVENT = "planned_maintenance"
HOLIDAY_EVENT = "holiday"


def _days(
    base: npt.NDArray[np.datetime64] | np.datetime64, days: npt.NDArray[np.int64]
) -> npt.NDArray[np.datetime64]:
    return np.asarray(base + days.astype("timedelta64[D]"), dtype="datetime64[ns]")


def build_quality_inspections(
    config: GeneratorConfig,
    rng: np.random.Generator,
    runs: pd.DataFrame,
    dims: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """One to three inspections per run with gauge, measurement and pass flag."""
    counts = 1 + rng.binomial(INSPECTION_EXTRA_TRIALS, INSPECTION_EXTRA_PROBABILITY, size=len(runs))
    run_idx = np.repeat(np.arange(len(runs)), counts)
    n = len(run_idx)
    operators = dims["operators"]
    inspectors = operators.loc[operators["role"] == "inspector", "operator_id"].to_numpy()
    if len(inspectors) == 0:
        inspectors = operators["operator_id"].to_numpy()
    products = dims["products"].set_index("product_id")
    product_pos = products.index.get_indexer(pd.Index(runs["product_id"].to_numpy()[run_idx]))
    target = products["spec_target_mm"].to_numpy(dtype=float)[product_pos]
    tolerance = products["spec_tolerance_mm"].to_numpy(dtype=float)[product_pos]
    line_index = pd.Index(dims["production_lines"]["line_id"])
    line_idx = line_index.get_indexer(pd.Index(runs["line_id"].to_numpy()[run_idx]))
    gauge_ids = make_ids("G", config.lines * GAUGES_PER_LINE, 3)
    gauge = gauge_ids[line_idx * GAUGES_PER_LINE + rng.integers(0, GAUGES_PER_LINE, size=n)]
    measured = target + rng.normal(size=n) * tolerance * MEASUREMENT_SD_IN_TOLERANCES
    passed = np.abs(measured - target) <= tolerance
    defects = np.array(DEFECT_TYPES, dtype=object)[rng.integers(0, len(DEFECT_TYPES), size=n)]
    return pd.DataFrame(
        {
            "inspection_id": np.arange(1, n + 1, dtype=np.int64),
            "run_id": runs["run_id"].to_numpy()[run_idx],
            "ts": runs["ts"].to_numpy()[run_idx] + np.timedelta64(config.grain_minutes, "m"),
            "inspector_id": inspectors[rng.integers(0, len(inspectors), size=n)],
            "gauge_id": gauge,
            "measured_value": np.round(measured, 4),
            "passed": passed,
            "defect_type": pd.Series(defects, dtype="str").mask(passed),
            "source_system": SOURCE_SYSTEM,
        }
    )


def build_downtime_events(
    config: GeneratorConfig, rng: np.random.Generator, calendar: Calendar, machines: pd.DataFrame
) -> pd.DataFrame:
    """Sparse downtime events drawn per machine-period with reason-specific durations."""
    n_cells = config.machines * config.periods
    probability = DOWNTIME_PER_MACHINE_HOUR * config.grain_minutes / MINUTES_PER_HOUR
    cell = np.flatnonzero(rng.random(n_cells) < probability)
    period, machine_idx = np.divmod(cell, config.machines)
    k = len(cell)
    reason_idx = rng.choice(len(DOWNTIME_REASONS), size=k, p=DOWNTIME_REASON_P)
    start_offset_s = rng.random(k) * (config.grain_minutes - 1) * SECONDS_PER_MINUTE
    span = DOWNTIME_MINUTES_HIGH[reason_idx] - DOWNTIME_MINUTES_LOW[reason_idx]
    duration_min = DOWNTIME_MINUTES_LOW[reason_idx] + rng.random(k) * span
    start = calendar.ts[period] + start_offset_s.astype(np.int64).astype("timedelta64[s]")
    end = start + (duration_min * SECONDS_PER_MINUTE).astype(np.int64).astype("timedelta64[s]")
    return pd.DataFrame(
        {
            "event_id": np.arange(1, k + 1, dtype=np.int64),
            "machine_id": machines["machine_id"].to_numpy()[machine_idx],
            "start_ts": start.astype("datetime64[ns]"),
            "end_ts": end.astype("datetime64[ns]"),
            "reason_code": np.array(DOWNTIME_REASONS, dtype=str)[reason_idx],
            "duration_min": np.round(duration_min, 1),
        }
    )


def _last_window_day(config: GeneratorConfig) -> np.datetime64:
    return np.datetime64(config.start, "ns") + np.timedelta64(config.days - 1, "D")


def _planned_maintenance(
    config: GeneratorConfig, rng: np.random.Generator, calendar: Calendar, machines: pd.DataFrame
) -> pd.DataFrame:
    schedule = calendar.line_maintenance
    machine_line = np.arange(len(machines)) // config.machines_per_line
    rows = pd.DataFrame({"line_idx": machine_line, "machine_id": machines["machine_id"]}).merge(
        schedule, on="line_idx", how="inner"
    )
    n = len(rows)
    lag = PLANNED_PERFORMED_LAG_DAYS[rng.integers(0, len(PLANNED_PERFORMED_LAG_DAYS), size=n)]
    performed = pd.Series(
        np.minimum(_days(rows["scheduled_date"].to_numpy(), lag), _last_window_day(config))
    )
    skipped = rng.random(n) < PLANNED_SKIPPED_SHARE
    return pd.DataFrame(
        {
            "machine_id": rows["machine_id"].to_numpy(),
            "kind": "planned",
            "scheduled_date": rows["scheduled_date"].to_numpy(),
            "performed_date": performed.mask(skipped),
            "parts_replaced": PLANNED_PARTS[rng.integers(0, len(PLANNED_PARTS), size=n)],
        }
    )


def _unplanned_maintenance(
    config: GeneratorConfig, rng: np.random.Generator, downtime: pd.DataFrame
) -> pd.DataFrame:
    breakdowns = downtime.loc[downtime["reason_code"] == "breakdown"]
    picked = breakdowns.loc[rng.random(len(breakdowns)) < UNPLANNED_SHARE_OF_BREAKDOWNS]
    n = len(picked)
    scheduled = picked["start_ts"].to_numpy().astype("datetime64[D]").astype("datetime64[ns]")
    lag = rng.integers(0, 2, size=n)
    return pd.DataFrame(
        {
            "machine_id": picked["machine_id"].to_numpy(),
            "kind": "unplanned",
            "scheduled_date": scheduled,
            "performed_date": np.minimum(_days(scheduled, lag), _last_window_day(config)),
            "parts_replaced": UNPLANNED_PARTS[rng.integers(0, len(UNPLANNED_PARTS), size=n)],
        }
    )


def build_maintenance_events(
    config: GeneratorConfig,
    rng: np.random.Generator,
    calendar: Calendar,
    machines: pd.DataFrame,
    downtime: pd.DataFrame,
) -> pd.DataFrame:
    """Monthly planned maintenance per machine plus unplanned work after breakdowns."""
    planned = _planned_maintenance(config, rng, calendar, machines)
    unplanned = _unplanned_maintenance(config, rng, downtime)
    combined = pd.concat([planned, unplanned], ignore_index=True)
    ordered = combined.sort_values(["scheduled_date", "machine_id", "kind"], kind="stable")
    ordered = ordered.reset_index(drop=True)
    ordered = ordered.assign(parts_replaced=ordered["parts_replaced"].astype("str"))
    ids = np.arange(1, len(ordered) + 1, dtype=np.int64)
    return ordered.assign(event_id=ids)[["event_id", *combined.columns]]


def build_gauge_calibrations(config: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    """Quarterly calibrations per gauge, staggered across the first interval."""
    n_gauges = config.lines * GAUGES_PER_LINE
    first_due = rng.integers(0, CALIBRATION_INTERVAL_DAYS, size=n_gauges)
    cycles = config.days // CALIBRATION_INTERVAL_DAYS + 1
    due_day = (first_due[:, None] + CALIBRATION_INTERVAL_DAYS * np.arange(cycles)[None, :]).ravel()
    gauge_idx = np.repeat(np.arange(n_gauges), cycles)
    keep = due_day < config.days
    due_day, gauge_idx = due_day[keep], gauge_idx[keep]
    n = len(due_day)
    lag = CALIBRATION_LAG_DAYS[rng.integers(0, len(CALIBRATION_LAG_DAYS), size=n)]
    start = np.datetime64(config.start, "ns")
    order = np.lexsort((gauge_idx, due_day))
    return pd.DataFrame(
        {
            "calibration_id": np.arange(1, n + 1, dtype=np.int64),
            "gauge_id": make_ids("G", n_gauges, 3)[gauge_idx][order],
            "due_date": _days(start, due_day)[order],
            "performed_date": _days(start, due_day + lag)[order],
            "offset_found": np.round(rng.normal(0.0, CALIBRATION_OFFSET_SD, size=n), 4)[order],
        }
    )


def _claim_counts(
    rng: np.random.Generator, runs: pd.DataFrame, complexity: pd.Series[float]
) -> npt.NDArray[np.int64]:
    position = complexity.index.get_indexer(pd.Index(runs["product_id"]))
    run_complexity = complexity.to_numpy(dtype=float)[position]
    expected = CLAIMS_PER_UNIT * runs["units_produced"].to_numpy() * (0.8 + 0.1 * run_complexity)
    return np.asarray(rng.poisson(expected), dtype=np.int64)


def _burn_in_claims(
    config: GeneratorConfig,
    rng: np.random.Generator,
    runs: pd.DataFrame,
    complexity: pd.Series[float],
) -> pd.DataFrame:
    """Claims from virtual production in the ``CLAIM_BURN_IN_DAYS`` before the window.

    The first in-window days are used as the template for that earlier production
    (tiled backwards over the burn-in length), so claim arrivals are stationary from
    day 1. The virtual runs do not exist as rows, so these claims carry a null
    ``run_id`` (the unit was produced before the history starts); claims that would
    be dated before the window are dropped.
    """
    template_days = min(config.days, CLAIM_BURN_IN_DAYS)
    window_start = np.datetime64(config.start, "ns")
    template = runs.loc[runs["ts"] < window_start + np.timedelta64(template_days, "D")]
    # Tile the template backwards until the whole burn-in period is covered.
    n_tiles = -(-CLAIM_BURN_IN_DAYS // template_days)
    tiles = [_claim_counts(rng, template, complexity) for _ in range(n_tiles)]
    counts = np.concatenate(tiles)
    idx = np.repeat(np.tile(np.arange(len(template)), n_tiles), counts)
    tile_no = np.repeat(np.repeat(np.arange(1, n_tiles + 1), len(template)), counts)
    virtual_day = template["ts"].to_numpy()[idx].astype("datetime64[D]").astype("datetime64[ns]")
    virtual_day = virtual_day - (tile_no * template_days).astype("timedelta64[D]")
    lag = rng.integers(CLAIM_LAG_MIN_DAYS, CLAIM_LAG_MAX_DAYS + 1, size=len(idx))
    return pd.DataFrame(
        {
            "product_id": template["product_id"].to_numpy()[idx],
            "run_id": pd.array([pd.NA] * len(idx), dtype="Int64"),
            "claim_date": _days(virtual_day, lag),
        }
    )


def build_warranty_claims(
    config: GeneratorConfig,
    rng: np.random.Generator,
    runs: pd.DataFrame,
    products: pd.DataFrame,
) -> pd.DataFrame:
    """Claims arrive 30-90 days after the run; rate scales with units and complexity.

    Claims from in-window runs are combined with burn-in claims (see
    ``_burn_in_claims``, null ``run_id``) and clipped to the window, so there are no
    future-dated rows and the daily claim rate is stationary from the first day.
    """
    complexity = products.set_index("product_id")["complexity_score"]
    counts = _claim_counts(rng, runs, complexity)
    run_idx = np.repeat(np.arange(len(runs)), counts)
    run_day = runs["ts"].to_numpy()[run_idx].astype("datetime64[D]").astype("datetime64[ns]")
    lag = rng.integers(CLAIM_LAG_MIN_DAYS, CLAIM_LAG_MAX_DAYS + 1, size=len(run_idx))
    in_window = pd.DataFrame(
        {
            "product_id": runs["product_id"].to_numpy()[run_idx],
            "run_id": pd.array(runs["run_id"].to_numpy()[run_idx], dtype="Int64"),
            "claim_date": _days(run_day, lag),
        }
    )
    burn_in = _burn_in_claims(config, rng, runs, complexity)
    combined = pd.concat([burn_in, in_window], ignore_index=True)
    window_start = np.datetime64(config.start, "ns")
    window_end = window_start + np.timedelta64(config.days, "D")
    keep = (combined["claim_date"] >= window_start) & (combined["claim_date"] < window_end)
    kept = combined.loc[keep].sort_values("claim_date", kind="stable").reset_index(drop=True)
    n = len(kept)
    defects = np.array(WARRANTY_DEFECT_TYPES, dtype=str)
    return pd.DataFrame(
        {
            "claim_id": np.arange(1, n + 1, dtype=np.int64),
            "product_id": kept["product_id"].to_numpy(),
            "run_id": kept["run_id"],
            "claim_date": kept["claim_date"].to_numpy(),
            "defect_type": defects[rng.integers(0, len(defects), size=n)],
            "claim_cost": np.round(rng.uniform(40.0, 600.0, size=n), 2),
        }
    )


def build_business_events(
    config: GeneratorConfig, calendar: Calendar, plants: pd.DataFrame
) -> pd.DataFrame:
    """Holidays and per-plant monthly maintenance windows (no randomness)."""
    holidays = pd.DataFrame(
        {
            "event_date": calendar.holiday_dates,
            "event_type": HOLIDAY_EVENT,
            "description": "Public holiday: all plants closed",
            "plant_id": None,
        }
    )
    schedule = calendar.line_maintenance.assign(
        plant_idx=lambda f: f["line_idx"] // config.lines_per_plant,
        month=lambda f: f["scheduled_date"].dt.to_period("M"),
    )
    windows = schedule.groupby(["plant_idx", "month"], sort=True)["scheduled_date"].min()
    plant_ids = plants["plant_id"].to_numpy()[windows.index.get_level_values("plant_idx")]
    maintenance = pd.DataFrame(
        {
            "event_date": windows.to_numpy(),
            "event_type": MONTHLY_MAINTENANCE_EVENT,
            "description": [f"Planned maintenance window starts at plant {p}" for p in plant_ids],
            "plant_id": plant_ids,
        }
    )
    combined = pd.concat([holidays, maintenance], ignore_index=True)
    ordered = combined.sort_values(["event_date", "event_type"], kind="stable").reset_index(
        drop=True
    )
    ordered = ordered.assign(plant_id=ordered["plant_id"].astype("str"))
    ids = np.arange(1, len(ordered) + 1, dtype=np.int64)
    return ordered.assign(event_id=ids)[["event_id", *combined.columns]]


def build_event_tables(
    config: GeneratorConfig,
    rng: np.random.Generator,
    calendar: Calendar,
    runs: pd.DataFrame,
    dims: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Build the event-grain tables in a fixed order."""
    inspections = build_quality_inspections(config, rng, runs, dims)
    downtime = build_downtime_events(config, rng, calendar, dims["machines"])
    maintenance = build_maintenance_events(config, rng, calendar, dims["machines"], downtime)
    calibrations = build_gauge_calibrations(config, rng)
    claims = build_warranty_claims(config, rng, runs, dims["products"])
    business = build_business_events(config, calendar, dims["plants"])
    return {
        "quality_inspections": inspections,
        "downtime_events": downtime,
        "maintenance_events": maintenance,
        "gauge_calibrations": calibrations,
        "warranty_claims": claims,
        "business_events": business,
    }
