"""Robustness contract of the easy incidents: multi-seed visibility, independence, hints."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

SEEDS = range(12)
PRE_DAYS = 56
Z_MIN = 3.0
CLAIM_TOLERANCE = 0.05
CYCLE_BAND = 0.05
SIZE_TOLERANCE = 0.05
MIN_E3_SIZE = 0.25


def _assert_all_visible(days: int, seed: int) -> None:
    from rootcause.evals.manufacturing import GeneratorConfig, generate_with_manifest
    from rootcause.evals.manufacturing.incidents import assert_visible, instantiate

    config = GeneratorConfig(seed=seed, days=days)
    tables, cases = generate_with_manifest(config)

    assert len(cases) == 6
    for incident in instantiate(config):
        report = assert_visible(tables, incident)
        assert report.visible is True, (seed, days, report)


@pytest.mark.unit
@pytest.mark.parametrize("seed", SEEDS)
def test_every_incident_is_visible_across_seeds_120_days(seed: int) -> None:
    _assert_all_visible(120, seed)


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_every_incident_is_visible_across_seeds_365_days(seed: int) -> None:
    _assert_all_visible(365, seed)


@pytest.mark.unit
def test_e2_metric_grain_is_plant_by_shift_by_day(config120: Any) -> None:
    from rootcause.evals.manufacturing import generate_with_manifest

    _, cases = generate_with_manifest(config120)

    e2 = [c for c in cases if c.archetype == "E2"]
    assert len(e2) == 2
    assert all("shift" in c.metric.grain for c in e2)


@pytest.mark.unit
def test_e2_robust_z_is_at_least_three_on_seed_42() -> None:
    from rootcause.evals.manufacturing import GeneratorConfig, generate_with_manifest
    from rootcause.evals.manufacturing.incidents import assert_visible, instantiate

    config = GeneratorConfig(seed=42, days=120)
    tables, _ = generate_with_manifest(config)

    for incident in instantiate(config):
        if incident.archetype == "E2":
            assert assert_visible(tables, incident).z >= Z_MIN, incident.case_id


ROLLING_NOISE_SEED = 3
SHIFT_MADS = 5.0
MAD_TO_SIGMA = 1.4826


def _incident_for(window_days: int, direction: str = "up") -> Any:
    window_start = date(2024, 1, 1) + timedelta(days=PRE_DAYS)
    return SimpleNamespace(
        case_id="synthetic",
        window=(window_start, window_start + timedelta(days=window_days - 1)),
        direction=direction,
        weekday_adjusted=False,
    )


def _series(values: np.ndarray) -> pd.Series[float]:
    index = pd.date_range(date(2024, 1, 1), periods=len(values), freq="D")
    return pd.Series(values, index=index, dtype=float)


def _rolling_stats(pre: np.ndarray, window_days: int) -> tuple[float, float]:
    """Median and MAD of the rolling L-day means inside the pre-window (documented formula)."""
    rolling = pd.Series(pre).rolling(window_days).mean().dropna().to_numpy()
    center = float(np.median(rolling))
    mad = float(np.median(np.abs(rolling - center)))
    return center, max(mad, 0.01 * center)


def _noisy_pre(seed: int = ROLLING_NOISE_SEED) -> np.ndarray:
    return 100.0 + np.random.default_rng(seed).normal(0.0, 5.0, PRE_DAYS)


@pytest.mark.unit
@pytest.mark.parametrize("window_days", [1, 5, 14])
@pytest.mark.parametrize("seed", range(6))
def test_robust_z_of_a_stationary_noisy_series_stays_below_three(
    window_days: int, seed: int
) -> None:
    from rootcause.evals.manufacturing.incidents.visibility import robust_z

    rng = np.random.default_rng(100 + seed)
    values = 100.0 + rng.normal(0.0, 5.0, PRE_DAYS + window_days)

    z = robust_z(_incident_for(window_days), _series(values))

    assert abs(z) < Z_MIN


@pytest.mark.unit
@pytest.mark.parametrize("window_days", [5, 14])
def test_robust_z_recovers_a_shift_of_five_robust_sigmas(window_days: int) -> None:
    from rootcause.evals.manufacturing.incidents.visibility import robust_z

    pre = _noisy_pre()
    center, mad = _rolling_stats(pre, window_days)
    window = np.full(window_days, center + SHIFT_MADS * MAD_TO_SIGMA * mad)

    z = robust_z(_incident_for(window_days), _series(np.concatenate([pre, window])))

    assert z == pytest.approx(SHIFT_MADS, rel=0.10)


@pytest.mark.unit
@pytest.mark.parametrize("window_days", [1, 5, 14])
@pytest.mark.parametrize("direction", ["up", "down"])
def test_robust_z_equals_the_documented_formula(window_days: int, direction: str) -> None:
    from rootcause.evals.manufacturing.incidents.visibility import robust_z

    pre = _noisy_pre()
    window = 100.0 + np.random.default_rng(9).normal(8.0, 3.0, window_days)
    center, mad = _rolling_stats(pre, window_days)
    sign = 1.0 if direction == "up" else -1.0
    expected = sign * (float(window.mean()) - center) / (MAD_TO_SIGMA * mad)

    z = robust_z(_incident_for(window_days, direction), _series(np.concatenate([pre, window])))

    assert z == pytest.approx(expected, rel=1e-6)


@pytest.mark.unit
def test_robust_z_floors_the_mad_at_one_percent_of_the_median() -> None:
    from rootcause.evals.manufacturing.incidents.visibility import robust_z

    pre = np.full(PRE_DAYS, 100.0)
    window = np.full(5, 110.0)

    z = robust_z(_incident_for(5), _series(np.concatenate([pre, window])))

    assert z == pytest.approx(10.0 / (MAD_TO_SIGMA * 1.0), rel=1e-6)


@pytest.mark.unit
def test_visibility_report_exposes_z_as_a_float(config120: Any, applied120: Any) -> None:
    from rootcause.evals.manufacturing.incidents import assert_visible, instantiate

    incident = instantiate(config120)[0]
    report = assert_visible(applied120[incident.case_id], incident)

    assert isinstance(report.z, float)


def _single_e3(config: Any, split: str = "dev") -> tuple[Any, dict[str, pd.DataFrame]]:
    from rootcause.evals.manufacturing import generate
    from rootcause.evals.manufacturing.incidents import instantiate

    incident = next(i for i in instantiate(config) if i.case_id == f"E3-{split}")
    return incident, generate(config, incidents=(incident,))


@pytest.mark.unit
@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_e3_switched_runs_keep_the_baseline_cycle_time_ratio(
    config120: Any, baseline120: Any, split: str
) -> None:
    incident, tables = _single_e3(config120, split)
    line = incident.segment["line_id"]
    std = tables["products"].set_index("product_id")["standard_cycle_time_s"]
    runs = tables["production_runs"].set_index("run_id")
    base = baseline120["production_runs"].set_index("run_id")
    on_line = runs["line_id"] == line
    switched = on_line & (runs["product_id"] != base["product_id"].reindex(runs.index))
    ratio = runs["cycle_time_s"] / runs["product_id"].map(std)

    assert switched.any()
    start, end = pd.Timestamp(incident.window[0]), pd.Timestamp(incident.window[1])
    outside = switched & ((runs["ts"] < start) | (runs["ts"] >= end + pd.Timedelta(days=1)))
    assert outside.any(), "overlap outside the window must also be exercised"
    unswitched_mean = float(ratio[on_line & ~switched].mean())
    for mask in (switched, outside):
        assert float(ratio[mask].mean()) == pytest.approx(unswitched_mean, rel=CYCLE_BAND)


@pytest.mark.unit
@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_e3_expected_effect_matches_the_achieved_cycle_time_ratio(
    config120: Any, split: str
) -> None:
    incident, tables = _single_e3(config120, split)
    runs = tables["production_runs"]
    line = runs[runs["line_id"] == incident.segment["line_id"]]
    start, end = pd.Timestamp(incident.window[0]), pd.Timestamp(incident.window[1])
    in_win = (line["ts"] >= start) & (line["ts"] < end + pd.Timedelta(days=1))
    pre = (line["ts"] >= start - pd.Timedelta(days=PRE_DAYS)) & (line["ts"] < start)

    ratio = float(line.loc[in_win, "cycle_time_s"].mean() / line.loc[pre, "cycle_time_s"].mean())

    assert 1.2 <= ratio <= 1.4
    assert incident.manifest_entry().expected_effect.size == pytest.approx(
        ratio - 1.0, abs=SIZE_TOLERANCE
    )


@pytest.mark.unit
@pytest.mark.parametrize("seed", SEEDS)
def test_e3_is_placed_only_where_a_ratio_of_1_25_is_achievable(seed: int) -> None:
    from rootcause.evals.manufacturing import GeneratorConfig, generate_with_manifest

    _, cases = generate_with_manifest(GeneratorConfig(seed=seed, days=120))

    e3 = [c for c in cases if c.archetype == "E3"]
    assert len(e3) == 2
    for case in e3:
        assert case.expected_effect.size >= MIN_E3_SIZE, (seed, case.case_id)


@pytest.mark.unit
def test_warranty_claims_follow_the_product_of_their_run(config120: Any) -> None:
    from rootcause.evals.manufacturing import generate
    from rootcause.evals.manufacturing.incidents import instantiate

    tables = generate(config120, incidents=instantiate(config120))
    claims = tables["warranty_claims"].dropna(subset=["run_id"])
    runs = tables["production_runs"].set_index("run_id")["product_id"]

    run_product = claims["run_id"].astype("int64").map(runs)

    assert len(claims) > 0
    assert (claims["product_id"].to_numpy() == run_product.to_numpy()).all()


@pytest.mark.unit
def test_warranty_claims_of_switched_runs_carry_the_new_product(
    config120: Any, baseline120: Any
) -> None:
    incident, tables = _single_e3(config120)
    runs = tables["production_runs"].set_index("run_id")
    base = baseline120["production_runs"].set_index("run_id")
    changed = runs.index[runs["product_id"] != base["product_id"].reindex(runs.index)]
    claims = tables["warranty_claims"]
    claims = claims[claims["run_id"].isin(changed)]

    assert len(changed) > 0
    assert len(claims) > 0
    assert (
        claims["product_id"].to_numpy()
        == claims["run_id"].astype("int64").map(runs["product_id"]).to_numpy()
    ).all()


@pytest.mark.unit
@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_e3_is_independent_of_the_other_incidents(config120: Any, split: str) -> None:
    from rootcause.evals.manufacturing import generate
    from rootcause.evals.manufacturing.incidents import instantiate

    incident, alone = _single_e3(config120, split)
    together = generate(config120, incidents=instantiate(config120))

    def window_rows(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
        runs = tables["production_runs"]
        start, end = pd.Timestamp(incident.window[0]), pd.Timestamp(incident.window[1])
        mask = (
            (runs["line_id"] == incident.segment["line_id"])
            & (runs["ts"] >= start)
            & (runs["ts"] < end + pd.Timedelta(days=1))
        )
        return pd.DataFrame(runs[mask].sort_values("run_id").reset_index(drop=True))

    left, right = window_rows(alone), window_rows(together)

    assert len(left) > 0
    pd.testing.assert_frame_equal(left, right)
    assert left["material_lot_id"].equals(right["material_lot_id"])


def _lines_of(incident: Any, tables: Mapping[str, pd.DataFrame]) -> set[str]:
    if "line_id" in incident.segment:
        return {incident.segment["line_id"]}
    lines = tables["production_lines"]
    return set(lines.loc[lines["plant_id"] == incident.segment["plant_id"], "line_id"])


@pytest.mark.unit
def test_no_window_intrudes_into_another_incidents_pre_window(
    config120: Any, baseline120: Any
) -> None:
    from rootcause.evals.manufacturing.incidents import instantiate

    incidents = instantiate(config120)
    for first in incidents:
        pre_start = first.window[0] - timedelta(days=PRE_DAYS)
        for other in incidents:
            if other is first or not (
                _lines_of(first, baseline120) & _lines_of(other, baseline120)
            ):
                continue
            intersects = other.window[0] < first.window[0] and other.window[1] >= pre_start
            assert not intersects, (first.case_id, other.case_id)


@pytest.mark.unit
def test_registry_reserves_a_full_pre_window_before_the_first_window(config120: Any) -> None:
    from rootcause.evals.manufacturing.incidents import instantiate

    earliest = config120.start_date + timedelta(days=PRE_DAYS)

    assert all(i.window[0] >= earliest for i in instantiate(config120))


@pytest.fixture(scope="module")
def manifests120(config120: Any) -> dict[str, Any]:
    from rootcause.evals.manufacturing.incidents import instantiate

    return {i.case_id: i for i in instantiate(config120)}


@pytest.mark.unit
@pytest.mark.parametrize(
    "case_id", [f"{a}-{s}" for a in ("E1", "E2", "E3") for s in ("dev", "heldout")]
)
def test_sql_hints_are_scoped_to_the_segment(case_id: str, manifests120: dict[str, Any]) -> None:
    incident = manifests120[case_id]
    scope = next(iter(incident.segment.values()))
    entry = incident.manifest_entry()

    assert scope in entry.counterfactual.sql_hint
    assert all(scope in step.sql_hint for step in entry.chain)


@pytest.mark.unit
@pytest.mark.parametrize("case_id", ["E2-dev", "E2-heldout"])
def test_e2_downtime_chain_sql_filters_by_plant(case_id: str, manifests120: dict[str, Any]) -> None:
    entry = manifests120[case_id].manifest_entry()

    downtime = [s for s in entry.chain if "downtime_events" in s.evidence_tables]

    assert downtime
    assert all("plant" in s.sql_hint.lower() for s in downtime)


@pytest.mark.unit
@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_keywords_include_the_segment_ids(
    split: str, manifests120: dict[str, Any], baseline120: Any
) -> None:
    from rootcause.evals.manufacturing.incidents.product_mix import top_family

    for archetype in ("E1", "E3"):
        incident = manifests120[f"{archetype}-{split}"]
        assert incident.segment["line_id"] in incident.manifest_entry().keywords
    e2 = manifests120[f"E2-{split}"]
    e2_keywords = e2.manifest_entry().keywords
    assert e2.segment["plant_id"] in e2_keywords
    assert e2.params["shift"] in e2_keywords
    e3_keywords = manifests120[f"E3-{split}"].manifest_entry().keywords
    assert top_family(baseline120["products"]) in e3_keywords


@pytest.mark.unit
def test_manifest_nested_fields_are_immutable(manifests120: dict[str, Any]) -> None:
    entry: Any = manifests120["E1-dev"].manifest_entry()

    for name in ("chain", "decoys", "keywords", "benign_events_in_window"):
        assert isinstance(getattr(entry, name), tuple), name
    with pytest.raises((AttributeError, TypeError)):
        entry.chain.append(entry.chain[0])
    with pytest.raises((AttributeError, TypeError)):
        entry.segment_filters["line_id"] = "X"
    with pytest.raises((AttributeError, TypeError)):
        entry.keywords[0] = "x"


@pytest.mark.unit
def test_manifest_still_serialises_to_json_lists_and_dicts(
    tmp_path: Path, manifests120: dict[str, Any]
) -> None:
    from rootcause.evals.manufacturing.incidents import write_cases_json

    entries = [i.manifest_entry() for i in manifests120.values()]
    write_cases_json(entries, tmp_path / "cases.json")

    loaded = json.loads((tmp_path / "cases.json").read_text(encoding="utf-8"))

    assert len(loaded) == 6
    assert isinstance(loaded[0]["chain"], list)
    assert isinstance(loaded[0]["segment_filters"], dict)
    assert isinstance(dataclasses.asdict(entries[0])["metric"], dict)
