"""Registry of archetypes and deterministic placement of two instances each.

Each half of the usable period (after a 28-day pre-window) hosts one instance
per archetype in its own sub-slot, so windows never overlap. The first half is
the development split, the second the held-out split.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from rootcause_evals.manufacturing.config import GeneratorConfig
from rootcause_evals.manufacturing.dimensions import make_ids
from rootcause_evals.manufacturing.incidents.base import Incident
from rootcause_evals.manufacturing.incidents.easy import CoolantPumpFailure, PowerOutage
from rootcause_evals.manufacturing.incidents.product_mix import ProductMixChange
from rootcause_evals.manufacturing.seasonality import holiday_dates, line_maintenance_schedule

PRE_WINDOW_DAYS = 28
SPLITS: tuple[str, str] = ("dev", "heldout")
WINDOW_DAYS = {"E1": 5, "E2": 1, "E3": 14}
OUTAGE_SHIFTS: tuple[str, ...] = ("day", "evening")
IDS_WIDTH_LINE = 2
IDS_WIDTH_PLANT = 2
IDS_WIDTH_MACHINE = 3
WEEKDAYS = 5

Slot = tuple[int, int]  # (first day offset, number of days) within the configured period
Factory = Callable[..., Incident]
ARCHETYPES: Mapping[str, Factory] = {
    "E1": CoolantPumpFailure,
    "E2": PowerOutage,
    "E3": ProductMixChange,
}


def _slots(config: GeneratorConfig) -> dict[str, dict[str, Slot]]:
    """Disjoint ``(offset, length)`` slots per split and archetype."""
    half = (config.days - PRE_WINDOW_DAYS) // 2
    length = half // len(ARCHETYPES)
    if length < max(WINDOW_DAYS.values()):
        raise ValueError(
            f"days={config.days} is too short to place the incidents; "
            f"need at least {PRE_WINDOW_DAYS + 2 * len(ARCHETYPES) * max(WINDOW_DAYS.values())}"
        )
    return {
        split: {
            archetype: (PRE_WINDOW_DAYS + s * half + a * length, length)
            for a, archetype in enumerate(ARCHETYPES)
        }
        for s, split in enumerate(SPLITS)
    }


def _window(
    config: GeneratorConfig, rng: np.random.Generator, slot: Slot, days: int
) -> tuple[date, date]:
    offset = slot[0] + int(rng.integers(0, slot[1] - days + 1))
    first = config.start_date + timedelta(days=offset)
    return first, first + timedelta(days=days - 1)


def _quiet_day(config: GeneratorConfig, rng: np.random.Generator, slot: Slot) -> date:
    """A weekday in the slot that is neither a holiday nor a planned maintenance day."""
    busy = {pd.Timestamp(d).date() for d in holiday_dates(config)}
    busy |= {d.date() for d in line_maintenance_schedule(config)["scheduled_date"]}
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


def _line_incidents(
    config: GeneratorConfig,
    rng: np.random.Generator,
    archetype: str,
    slots: dict[str, dict[str, Slot]],
) -> list[Incident]:
    """Two instances on two distinct lines (E1 also names the failing machine)."""
    lines = make_ids("L", config.lines, IDS_WIDTH_LINE)
    machines = make_ids("M", config.machines, IDS_WIDTH_MACHINE)
    picks = [int(i) for i in rng.choice(config.lines, size=2, replace=False)]
    result: list[Incident] = []
    for split, line_idx in zip(SPLITS, picks, strict=True):
        window = _window(config, rng, slots[split][archetype], WINDOW_DAYS[archetype])
        params: dict[str, Any] = {}
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
    config: GeneratorConfig, rng: np.random.Generator, slots: dict[str, dict[str, Slot]]
) -> list[Incident]:
    plants = make_ids("P", config.plants, IDS_WIDTH_PLANT)
    picks = [int(i) for i in rng.choice(config.plants, size=2, replace=config.plants < 2)]
    result: list[Incident] = []
    for split, plant_idx in zip(SPLITS, picks, strict=True):
        day = _quiet_day(config, rng, slots[split]["E2"])
        shift = OUTAGE_SHIFTS[int(rng.integers(0, len(OUTAGE_SHIFTS)))]
        plant_lines = range(
            plant_idx * config.lines_per_plant, (plant_idx + 1) * config.lines_per_plant
        )
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


def instantiate(
    config: GeneratorConfig,
    seed: int | None = None,
    archetypes: Iterable[str] | None = None,
) -> tuple[Incident, ...]:
    """Two incidents (dev, heldout) per requested archetype, deterministic from ``seed``.

    ``seed`` defaults to ``config.seed``; ``archetypes`` defaults to every registered one.
    """
    wanted = tuple(ARCHETYPES) if archetypes is None else tuple(archetypes)
    unknown = sorted(set(wanted) - set(ARCHETYPES))
    if unknown:
        raise ValueError(f"unknown incident archetype(s) {unknown}; known: {sorted(ARCHETYPES)}")
    if config.lines < 2:
        raise ValueError("incidents need at least two production lines")
    rng = np.random.default_rng([config.seed if seed is None else seed, len(ARCHETYPES)])
    slots = _slots(config)
    incidents: list[Incident] = []
    for archetype in ARCHETYPES:  # fixed draw order keeps instances stable across selections
        made = (
            _outages(config, rng, slots)
            if archetype == "E2"
            else _line_incidents(config, rng, archetype, slots)
        )
        if archetype in wanted:
            incidents.extend(made)
    return tuple(incidents)
