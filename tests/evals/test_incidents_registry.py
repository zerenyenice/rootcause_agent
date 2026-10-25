"""Incident registry: six easy incidents, deterministic placement, immutable apply."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

if TYPE_CHECKING:
    from collections.abc import Mapping

EASY = ("E1", "E2", "E3")
WINDOW_DAYS = {"E1": 5, "E2": 1, "E3": 14}
PRE_WINDOW_DAYS = 28


def _lines_of(incident: Any, baseline: Mapping[str, pd.DataFrame]) -> set[str]:
    if "line_id" in incident.segment:
        return {incident.segment["line_id"]}
    lines = baseline["production_lines"]
    plant = incident.segment["plant_id"]
    return set(lines.loc[lines["plant_id"] == plant, "line_id"])


@pytest.mark.unit
def test_archetypes_include_the_easy_ids() -> None:
    from rootcause.evals.manufacturing.incidents import ARCHETYPES

    assert {"E1", "E2", "E3"} <= set(ARCHETYPES)


@pytest.mark.unit
def test_instantiate_returns_two_instances_per_easy_archetype(incidents120: dict[str, Any]) -> None:
    incidents = list(incidents120.values())

    assert len(incidents) == 6
    for archetype in EASY:
        splits = sorted(i.split for i in incidents if i.archetype == archetype)
        assert splits == ["dev", "heldout"]
    assert {i.case_id for i in incidents} == {f"{a}-{s}" for a in EASY for s in ("dev", "heldout")}
    assert all(i.level == "easy" for i in incidents)


@pytest.mark.unit
def test_windows_are_dates_inside_the_config_with_room_for_a_pre_window(
    config120: Any, incidents120: dict[str, Any]
) -> None:
    first = config120.start_date
    last = date.fromordinal(first.toordinal() + config120.days - 1)
    for incident in incidents120.values():
        start, end = incident.window
        assert type(start) is date
        assert type(end) is date
        assert (end - start).days + 1 == WINDOW_DAYS[incident.archetype]
        assert (start - first).days >= PRE_WINDOW_DAYS
        assert end <= last


@pytest.mark.unit
def test_dev_windows_precede_heldout_windows_per_archetype(incidents120: dict[str, Any]) -> None:
    for archetype in EASY:
        dev = incidents120[f"{archetype}-dev"]
        heldout = incidents120[f"{archetype}-heldout"]
        assert dev.window[1] < heldout.window[0]


@pytest.mark.unit
def test_segments_are_distinct_and_valid(
    baseline120: dict[str, pd.DataFrame], incidents120: dict[str, Any]
) -> None:
    line_ids = set(baseline120["production_lines"]["line_id"])
    plant_ids = set(baseline120["plants"]["plant_id"])
    for archetype in ("E1", "E3"):
        dev, held = (incidents120[f"{archetype}-{s}"] for s in ("dev", "heldout"))
        assert {dev.segment["line_id"], held.segment["line_id"]} <= line_ids
        assert dev.segment["line_id"] != held.segment["line_id"]
    dev, held = incidents120["E2-dev"], incidents120["E2-heldout"]
    assert {dev.segment["plant_id"], held.segment["plant_id"]} <= plant_ids
    assert (dev.segment["plant_id"] != held.segment["plant_id"]) or (dev.window != held.window)


@pytest.mark.unit
def test_no_two_windows_overlap_on_the_same_line_or_plant(
    baseline120: dict[str, pd.DataFrame], incidents120: dict[str, Any]
) -> None:
    items = list(incidents120.values())
    for index, first in enumerate(items):
        for second in items[index + 1 :]:
            shares_lines = _lines_of(first, baseline120) & _lines_of(second, baseline120)
            overlap = first.window[0] <= second.window[1] and second.window[0] <= first.window[1]
            assert not (shares_lines and overlap), (first.case_id, second.case_id)


@pytest.mark.unit
def test_instantiate_is_deterministic(config120: Any) -> None:
    from rootcause.evals.manufacturing.incidents import instantiate

    def view(seed: int) -> list[tuple[Any, ...]]:
        return [(i.case_id, i.window, dict(i.segment)) for i in instantiate(config120, seed=seed)]

    assert view(7) == view(7)


@pytest.mark.unit
def test_apply_does_not_mutate_inputs_and_returns_all_tables(
    baseline120: dict[str, pd.DataFrame], incidents120: dict[str, Any]
) -> None:
    snapshot = {name: frame.copy(deep=True) for name, frame in baseline120.items()}
    for incident in incidents120.values():
        result = incident.apply(baseline120, np.random.default_rng(1))

        assert result is not baseline120
        assert len(result) == 20
        assert set(result) == set(baseline120)
    for name, frame in baseline120.items():
        assert_frame_equal(frame, snapshot[name])


@pytest.mark.unit
def test_apply_leaves_untouched_tables_equal_and_changes_the_affected_ones(
    baseline120: dict[str, pd.DataFrame], applied120: dict[str, dict[str, pd.DataFrame]]
) -> None:
    result = applied120["E1-dev"]

    assert_frame_equal(result["plants"], baseline120["plants"])
    assert_frame_equal(result["products"], baseline120["products"])
    assert not result["production_runs"].equals(baseline120["production_runs"])
    assert len(result["maintenance_events"]) == len(baseline120["maintenance_events"]) + 1


@pytest.mark.unit
def test_apply_is_deterministic_for_the_same_rng_seed(
    baseline120: dict[str, pd.DataFrame], incidents120: dict[str, Any]
) -> None:
    incident = incidents120["E1-heldout"]

    first = incident.apply(baseline120, np.random.default_rng(5))
    second = incident.apply(baseline120, np.random.default_rng(5))

    for name in first:
        assert_frame_equal(first[name], second[name])
