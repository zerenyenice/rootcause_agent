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
import numpy.typing as npt
import pandas as pd

from rootcause.evals.manufacturing.baseline import FAMILIES_PER_LINE, build_baseline
from rootcause.evals.manufacturing.config import GeneratorConfig
from rootcause.evals.manufacturing.dimensions import build_dimensions, make_ids
from rootcause.evals.manufacturing.incidents.base import PRE_WINDOW_DAYS, Incident
from rootcause.evals.manufacturing.incidents.easy import CoolantPumpFailure, PowerOutage
from rootcause.evals.manufacturing.incidents.product_mix import (
    ProductMixChange,
    top_family,
)
from rootcause.evals.manufacturing.seasonality import holiday_dates, line_maintenance_schedule

SPLITS: tuple[str, str] = ("dev", "heldout")
WINDOW_DAYS = {"E1": 5, "E2": 1, "E3": 14}
OUTAGE_SHIFTS: tuple[str, ...] = ("day", "evening")
IDS_WIDTH_LINE = 2
IDS_WIDTH_PLANT = 2
IDS_WIDTH_MACHINE = 3
WEEKDAYS = 5
MIN_E3_EFFECT = 0.25  # least achieved window/pre cycle time rise (ratio 1.25) E3 may be placed at
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
    windows: dict[str, tuple[date, date]] | None = None,
    effects: dict[str, float] | None = None,
) -> list[Incident]:
    """Two instances (dev, heldout) on the given lines; E1 also names the failing machine."""
    lines = make_ids("L", config.lines, IDS_WIDTH_LINE)
    machines = make_ids("M", config.machines, IDS_WIDTH_MACHINE)
    result: list[Incident] = []
    for split, line_idx in zip(SPLITS, picks, strict=True):
        window = (
            windows[split]
            if windows is not None
            else _window(config, rng, slots[split][archetype], WINDOW_DAYS[archetype])
        )
        params: dict[str, Any] = {}
        if archetype == "E3":
            params["family_id"] = family_id
            if effects is not None:
                params["expected_effect"] = round(effects[split], 4)
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


def _mix_headroom(config: GeneratorConfig, products: pd.DataFrame) -> list[float]:
    """Per line, the window/pre cycle time ratio the top family can reach on its product pool.

    Every switched order can at best take the top family's slowest product, so the ceiling
    is that product's standard cycle time over the pool's mean standard cycle time.
    """
    std = products["standard_cycle_time_s"].to_numpy(dtype=float)
    family_of_product = np.arange(len(std)) % config.families
    ceiling = float(std[(products["family_id"] == top_family(products)).to_numpy()].max())
    return [
        ceiling
        / float(
            std[
                np.isin(family_of_product, (line + np.arange(FAMILIES_PER_LINE)) % config.families)
            ].mean()
        )
        for line in range(config.lines)
    ]


def _plants_by_headroom(config: GeneratorConfig, products: pd.DataFrame) -> list[int]:
    """Candidate outage plants, the one leaving the most headroom lines for E3 first."""
    scores = _mix_headroom(config, products)

    def left_over(plant: int) -> float:
        rest = sorted(
            (scores[i] for i in range(config.lines) if i not in _plant_line_idx(config, plant)),
            reverse=True,
        )
        return sum(rest[: len(SPLITS)])

    return sorted(range(config.plants), key=lambda plant: (-left_over(plant), plant))


def _mix_windows(
    config: GeneratorConfig, rng: np.random.Generator, slots: dict[str, dict[str, Slot]]
) -> dict[str, tuple[date, date]]:
    return {split: _window(config, rng, slots[split]["E3"], WINDOW_DAYS["E3"]) for split in SPLITS}


def _achieved_effect(
    baseline: Mapping[str, pd.DataFrame],
    line_id: str,
    split: str,
    window: tuple[date, date],
    family_id: str,
) -> float:
    """Cycle time rise an E3 on ``line_id`` would achieve in ``window`` on the baseline."""
    probe = ProductMixChange(
        case_id=f"E3-{split}",
        split=split,
        window=window,
        segment={"line_id": line_id},
        params={"family_id": family_id},
    )
    return probe.achieved_effect(probe.apply(baseline, np.random.default_rng(0)))


def _daily_sums(
    config: GeneratorConfig, baseline: Mapping[str, pd.DataFrame]
) -> dict[int, npt.NDArray[np.float64]]:
    """Per line a ``(3, days)`` array: cycle time sum, ceiling cycle time sum and run count.

    The ceiling sum is each run's cycle time scaled as if its order took the top family's
    slowest product, so a window's ceiling mean over the pre-window mean bounds the rise.
    """
    runs, products = baseline["production_runs"], baseline["products"].set_index("product_id")
    top = products[products["family_id"] == top_family(baseline["products"])]
    scale = float(top["standard_cycle_time_s"].max()) / runs["product_id"].map(
        products["standard_cycle_time_s"]
    ).to_numpy(float)
    day = ((runs["ts"] - pd.Timestamp(config.start_date)).dt.days).to_numpy()
    line = runs["line_id"].map(
        {str(v): i for i, v in enumerate(baseline["production_lines"]["line_id"])}
    )
    sums: dict[int, npt.NDArray[np.float64]] = {}
    cycle = runs["cycle_time_s"].to_numpy(float)
    for i in range(config.lines):
        mask = (line == i).to_numpy()
        sums[i] = np.stack(
            [
                np.bincount(day[mask], weights=weights, minlength=config.days)
                for weights in (cycle[mask], (cycle * scale)[mask], np.ones(len(cycle))[mask])
            ]
        )
    return sums


def _ceiling_rise(sums: npt.NDArray[np.float64], first: int, length: int) -> float:
    """Upper bound of the window/pre cycle time rise for a window starting at day ``first``."""
    pre = sums[:, max(first - PRE_WINDOW_DAYS, 0) : first].sum(axis=1)
    window = sums[:, first : first + length].sum(axis=1)
    if pre[2] == 0 or window[2] == 0:
        return 0.0
    return float((window[1] / window[2]) / (pre[0] / pre[2]) - 1.0)


def _place_mix(
    config: GeneratorConfig,
    baseline: Mapping[str, pd.DataFrame],
    pool: list[int],
    split: str,
    drawn: tuple[date, date],
    slot: Slot,
    taken: list[int],
) -> tuple[int, tuple[date, date], float]:
    """Line and window for one E3 split: the drawn window if some free line reaches the rise,
    else the nearest window in the slot where one does; the line with the largest rise wins."""
    line_ids = make_ids("L", config.lines, IDS_WIDTH_LINE)
    family_id = top_family(baseline["products"])
    sums = _daily_sums(config, baseline)
    length = WINDOW_DAYS["E3"]
    drawn_offset = (drawn[0] - config.start_date).days
    offsets = sorted(
        range(slot[0], slot[0] + slot[1] - length + 1), key=lambda o: abs(o - drawn_offset)
    )
    best = 0.0
    for offset in offsets:
        window = (
            config.start_date + timedelta(days=offset),
            config.start_date + timedelta(days=offset + length - 1),
        )
        bounds = {
            line: _ceiling_rise(sums[line], offset, length) for line in pool if line not in taken
        }
        for line in sorted(bounds, key=lambda line: (-bounds[line], line)):
            if bounds[line] < MIN_E3_EFFECT:
                break
            effect = _achieved_effect(baseline, str(line_ids[line]), split, window, family_id)
            best = max(best, effect)
            if effect >= MIN_E3_EFFECT:
                return line, window, effect
    raise ValueError(
        f"product mix change E3-{split} reaches a cycle time rise of only {best:.2f} on any "
        f"line outside the outage plant (need {MIN_E3_EFFECT}); change the seed or configuration"
    )


def _line_picks(
    config: GeneratorConfig,
    rng: np.random.Generator,
    outage_plant: int,
    baseline: Mapping[str, pd.DataFrame],
    windows: dict[str, tuple[date, date]],
    slots: dict[str, dict[str, Slot]],
) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[date, date]], dict[str, float]]:
    """Distinct lines outside the outage plant, plus the final E3 windows.

    E3 takes lines where the top family achieves at least ``MIN_E3_EFFECT`` (``ValueError``
    otherwise); E1 takes two of the remaining lines at random.
    """
    outage_lines = _plant_line_idx(config, outage_plant)
    pool = [i for i in range(config.lines) if i not in outage_lines]
    needed = len(LINE_ARCHETYPES) * len(SPLITS)
    if len(pool) < needed:
        raise ValueError(
            f"incidents need {needed} production lines outside the outage plant, got {len(pool)}"
        )
    mix: list[int] = []
    placed: dict[str, tuple[date, date]] = {}
    effects: dict[str, float] = {}
    for split in SPLITS:
        line, window, effects[split] = _place_mix(
            config, baseline, pool, split, windows[split], slots[split]["E3"], mix
        )
        mix.append(line)
        placed[split] = window
    rest = [line for line in pool if line not in mix]
    coolant = [int(i) for i in rng.choice(rest, size=2, replace=False)]
    return {"E1": (coolant[0], coolant[1]), "E3": (mix[0], mix[1])}, placed, effects


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


def _place_lines(
    config: GeneratorConfig,
    rng: np.random.Generator,
    baseline: Mapping[str, pd.DataFrame],
    windows: dict[str, tuple[date, date]],
    slots: dict[str, dict[str, Slot]],
) -> tuple[int, dict[str, tuple[int, int]], dict[str, tuple[date, date]], dict[str, float]]:
    """Outage plant, line picks and E3 windows: first candidate plant where E3 can be placed."""
    error: ValueError | None = None
    for plant in _plants_by_headroom(config, baseline["products"]):
        try:
            return (plant, *_line_picks(config, rng, plant, baseline, windows, slots))
        except ValueError as exc:
            error = exc
    raise error or ValueError("no plant can host the outages")


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
    dims_rng = np.random.default_rng(config.seed)
    dims = build_dimensions(config, dims_rng)
    baseline = {**dims, **build_baseline(config, dims_rng, dims)}
    family_id = top_family(baseline["products"])
    windows = _mix_windows(config, rng, slots)
    outage_plant, picks, windows, effects = _place_lines(config, rng, baseline, windows, slots)
    incidents: list[Incident] = []
    for archetype in ARCHETYPES:  # fixed draw order keeps instances stable across selections
        made = (
            _outages(config, rng, slots, outage_plant)
            if archetype == "E2"
            else _line_incidents(
                config,
                rng,
                archetype,
                slots,
                picks[archetype],
                family_id,
                windows if archetype == "E3" else None,
                effects,
            )
        )
        if archetype in wanted:
            incidents.extend(made)
    _assert_pre_windows_clear(config, incidents)
    return tuple(incidents)
