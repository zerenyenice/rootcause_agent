"""Registry of archetypes and deterministic placement of two instances each.

Placement keeps every incident's pre-window (``PRE_WINDOW_DAYS`` before its window)
free of other incidents on the same line or plant:

- both power outages hit one plant, one early and one late enough that the later
  outage's pre-window starts after the earlier one ends;
- the four line incidents (E1 and E3, dev and heldout) use four distinct lines of the
  other plants, so they never share a line with each other or with an outage;
- the dev windows sit in the first half of the usable period and the heldout windows
  in the second half; the first window starts ``PRE_WINDOW_DAYS`` days after the start.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from rootcause_evals.manufacturing.baseline import FAMILIES_PER_LINE
from rootcause_evals.manufacturing.config import GeneratorConfig
from rootcause_evals.manufacturing.dimensions import build_dimensions, make_ids
from rootcause_evals.manufacturing.incidents.base import PRE_WINDOW_DAYS, Incident
from rootcause_evals.manufacturing.incidents.easy import CoolantPumpFailure, PowerOutage
from rootcause_evals.manufacturing.incidents.product_mix import (
    CYCLE_TIME_FACTOR,
    ProductMixChange,
    top_family,
)
from rootcause_evals.manufacturing.seasonality import holiday_dates, line_maintenance_schedule

SPLITS: tuple[str, str] = ("dev", "heldout")
WINDOW_DAYS = {"E1": 5, "E2": 1, "E3": 14}
OUTAGE_SHIFTS: tuple[str, ...] = ("day", "evening")
IDS_WIDTH_LINE = 2
IDS_WIDTH_PLANT = 2
IDS_WIDTH_MACHINE = 3
WEEKDAYS = 5
MIN_DAYS = 2 * PRE_WINDOW_DAYS + 2  # two outages one pre-window apart, each after its own
LINE_ARCHETYPES: tuple[str, ...] = ("E1", "E3")

Slot = tuple[int, int]  # (first day offset, number of days) within the configured period
Factory = Callable[..., Incident]
ARCHETYPES: Mapping[str, Factory] = {
    "E1": CoolantPumpFailure,
    "E2": PowerOutage,
    "E3": ProductMixChange,
}


def _slots(config: GeneratorConfig) -> dict[str, dict[str, Slot]]:
    """``(offset, length)`` slots per split and archetype.

    Line archetypes share the dev half and the heldout half (they sit on distinct lines,
    so their windows may coincide in time). Outage slots hug the two ends of the usable
    period, leaving at least one pre-window between the dev and the heldout outage.
    """
    if config.days < MIN_DAYS:
        raise ValueError(
            f"days={config.days} is too short to place the incidents; need at least {MIN_DAYS}"
        )
    half = (config.days - PRE_WINDOW_DAYS) // 2
    outage_len = (config.days - 2 * PRE_WINDOW_DAYS) // 2
    slots: dict[str, dict[str, Slot]] = {split: {} for split in SPLITS}
    for archetype in ARCHETYPES:
        for s, split in enumerate(SPLITS):
            if archetype == "E2":
                slots[split][archetype] = (
                    (PRE_WINDOW_DAYS, outage_len)
                    if s == 0
                    else (config.days - outage_len, outage_len)
                )
            else:
                slots[split][archetype] = (PRE_WINDOW_DAYS + s * half, half)
    return slots


def _window(
    config: GeneratorConfig, rng: np.random.Generator, slot: Slot, days: int
) -> tuple[date, date]:
    offset = slot[0] + int(rng.integers(0, slot[1] - days + 1))
    first = config.start_date + timedelta(days=offset)
    return first, first + timedelta(days=days - 1)


def _quiet_day(
    config: GeneratorConfig, rng: np.random.Generator, slot: Slot, line_idx: Iterable[int]
) -> date:
    """A weekday in the slot that is no holiday and has no planned maintenance on the lines."""
    busy = {pd.Timestamp(d).date() for d in holiday_dates(config)}
    schedule = line_maintenance_schedule(config)
    wanted = set(line_idx)
    busy |= {
        pd.Timestamp(day).date()
        for line, day in zip(
            schedule["line_idx"].to_numpy(), schedule["scheduled_date"].to_numpy(), strict=True
        )
        if int(line) in wanted
    }
    days = [config.start_date + timedelta(days=slot[0] + i) for i in range(slot[1])]
    free = [d for d in days if d.weekday() < WEEKDAYS and d not in busy]
    if not free:
        raise ValueError("no weekday without holiday or maintenance is free for the power outage")
    return free[int(rng.integers(0, len(free)))]


def _benign(
    config: GeneratorConfig, window: tuple[date, date], line_idx: Iterable[int]
) -> tuple[str, ...]:
    """Baseline non-anomalies (holidays, planned maintenance) falling in the window."""
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    events = [
        f"public holiday {pd.Timestamp(d).date().isoformat()}"
        for d in holiday_dates(config)
        if lo <= pd.Timestamp(d) <= hi
    ]
    schedule = line_maintenance_schedule(config)
    wanted = set(line_idx)
    lines = make_ids("L", config.lines, IDS_WIDTH_LINE)
    for line, scheduled in zip(
        schedule["line_idx"].to_numpy(), schedule["scheduled_date"].to_numpy(), strict=True
    ):
        stamp = pd.Timestamp(scheduled)
        if int(line) in wanted and lo <= stamp <= hi:
            events.append(
                f"planned maintenance on line {lines[int(line)]} {stamp.date().isoformat()}"
            )
    return tuple(events)


def _plant_line_idx(config: GeneratorConfig, plant_idx: int) -> range:
    return range(plant_idx * config.lines_per_plant, (plant_idx + 1) * config.lines_per_plant)


def _line_incidents(
    config: GeneratorConfig,
    rng: np.random.Generator,
    archetype: str,
    slots: dict[str, dict[str, Slot]],
    picks: tuple[int, int],
    family_id: str,
) -> list[Incident]:
    """Two instances (dev, heldout) on the given lines; E1 also names the failing machine."""
    lines = make_ids("L", config.lines, IDS_WIDTH_LINE)
    machines = make_ids("M", config.machines, IDS_WIDTH_MACHINE)
    result: list[Incident] = []
    for split, line_idx in zip(SPLITS, picks, strict=True):
        window = _window(config, rng, slots[split][archetype], WINDOW_DAYS[archetype])
        params: dict[str, Any] = {}
        if archetype == "E3":
            params["family_id"] = family_id
        if archetype == "E1":
            slot_in_line = int(rng.integers(0, config.machines_per_line))
            params["machine_id"] = str(machines[line_idx * config.machines_per_line + slot_in_line])
        result.append(
            ARCHETYPES[archetype](
                case_id=f"{archetype}-{split}",
                split=split,
                window=window,
                segment={"line_id": str(lines[line_idx])},
                params=params,
                benign_events=_benign(config, window, [line_idx]),
            )
        )
    return result


def _outages(
    config: GeneratorConfig,
    rng: np.random.Generator,
    slots: dict[str, dict[str, Slot]],
    plant_idx: int,
) -> list[Incident]:
    plants = make_ids("P", config.plants, IDS_WIDTH_PLANT)
    plant_lines = _plant_line_idx(config, plant_idx)
    result: list[Incident] = []
    for split in SPLITS:
        day = _quiet_day(config, rng, slots[split]["E2"], plant_lines)
        shift = OUTAGE_SHIFTS[int(rng.integers(0, len(OUTAGE_SHIFTS)))]
        result.append(
            ARCHETYPES["E2"](
                case_id=f"E2-{split}",
                split=split,
                window=(day, day),
                segment={"plant_id": str(plants[plant_idx])},
                params={"shift": shift},
                benign_events=_benign(config, (day, day), plant_lines),
            )
        )
    return result


def _mix_visibility(config: GeneratorConfig, products: pd.DataFrame) -> list[float]:
    """Per line, how visible a product mix change would be: signal-to-noise of the cycle time.

    The signal is the rise the top family allows (capped at the target factor) on the line's
    own product pool; the noise is the spread of standard cycle times in that pool.
    """
    std = products["standard_cycle_time_s"].to_numpy(dtype=float)
    family_of_product = np.arange(len(std)) % config.families
    top = std[(products["family_id"] == top_family(products)).to_numpy()]
    scores = []
    for line in range(config.lines):
        families = (line + np.arange(FAMILIES_PER_LINE)) % config.families
        pool = std[np.isin(family_of_product, families)]
        factor = min(CYCLE_TIME_FACTOR, float(top.max()) / float(pool.mean()))
        scores.append((factor - 1.0) * float(pool.mean()) / max(float(pool.std()), 1e-9))
    return scores


def _outage_plant(config: GeneratorConfig, products: pd.DataFrame) -> int:
    """The plant hosting both outages: the one that leaves the most visible lines for E3."""
    scores = _mix_visibility(config, products)
    mix_lines = len(SPLITS)

    def left_over(plant: int) -> float:
        rest = sorted(
            (scores[i] for i in range(config.lines) if i not in _plant_line_idx(config, plant)),
            reverse=True,
        )
        return sum(rest[:mix_lines])

    return max(range(config.plants), key=lambda plant: (left_over(plant), -plant))


def _line_picks(
    config: GeneratorConfig, rng: np.random.Generator, outage_plant: int, products: pd.DataFrame
) -> dict[str, tuple[int, int]]:
    """Distinct lines outside the outage plant: the most visible two for E3, random for E1."""
    outage_lines = _plant_line_idx(config, outage_plant)
    pool = [i for i in range(config.lines) if i not in outage_lines]
    needed = len(LINE_ARCHETYPES) * len(SPLITS)
    if len(pool) < needed:
        raise ValueError(
            f"incidents need {needed} production lines outside the outage plant, got {len(pool)}"
        )
    scores = _mix_visibility(config, products)
    ranked = sorted(pool, key=lambda line: (-scores[line], line))
    mix = (ranked[0], ranked[1])
    rest = [line for line in pool if line not in mix]
    coolant = [int(i) for i in rng.choice(rest, size=2, replace=False)]
    return {"E1": (coolant[0], coolant[1]), "E3": mix}


def _lines_of(config: GeneratorConfig, incident: Incident) -> set[str]:
    lines = make_ids("L", config.lines, IDS_WIDTH_LINE)
    if "line_id" in incident.segment:
        return {incident.segment["line_id"]}
    plants = make_ids("P", config.plants, IDS_WIDTH_PLANT)
    plant_idx = [str(p) for p in plants].index(incident.segment["plant_id"])
    return {str(lines[i]) for i in _plant_line_idx(config, plant_idx)}


def _assert_pre_windows_clear(config: GeneratorConfig, incidents: Iterable[Incident]) -> None:
    """No incident window may fall in another incident's pre-window on a shared line or plant."""
    items = list(incidents)
    for first in items:
        pre_start = first.window[0] - timedelta(days=PRE_WINDOW_DAYS)
        for other in items:
            intrudes = other.window[0] < first.window[0] and other.window[1] >= pre_start
            if (
                other is not first
                and intrudes
                and _lines_of(config, first) & _lines_of(config, other)
            ):
                raise ValueError(
                    f"incident {other.case_id} falls in the {PRE_WINDOW_DAYS}-day pre-window "
                    f"of {first.case_id}"
                )


def instantiate(
    config: GeneratorConfig,
    seed: int | None = None,
    archetypes: Iterable[str] | None = None,
) -> tuple[Incident, ...]:
    """Two incidents (dev, heldout) per requested archetype, deterministic from ``seed``.

    ``seed`` defaults to ``config.seed``; ``archetypes`` defaults to every registered one.
    Raises ``ValueError`` when the configuration cannot host the incidents.
    """
    wanted = tuple(ARCHETYPES) if archetypes is None else tuple(archetypes)
    unknown = sorted(set(wanted) - set(ARCHETYPES))
    if unknown:
        raise ValueError(f"unknown incident archetype(s) {unknown}; known: {sorted(ARCHETYPES)}")
    rng = np.random.default_rng([config.seed if seed is None else seed, len(ARCHETYPES)])
    slots = _slots(config)
    products = build_dimensions(config, np.random.default_rng(config.seed))["products"]
    outage_plant = _outage_plant(config, products)
    picks = _line_picks(config, rng, outage_plant, products)
    family_id = top_family(products)
    incidents: list[Incident] = []
    for archetype in ARCHETYPES:  # fixed draw order keeps instances stable across selections
        made = (
            _outages(config, rng, slots, outage_plant)
            if archetype == "E2"
            else _line_incidents(config, rng, archetype, slots, picks[archetype], family_id)
        )
        if archetype in wanted:
            incidents.extend(made)
    _assert_pre_windows_clear(config, incidents)
    return tuple(incidents)
