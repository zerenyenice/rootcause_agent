"""Visibility check, generate() integration and the --incidents CLI flag."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    import pandas as pd

Z_MIN = 3.0
ALL_IDS = [f"{a}-{s}" for a in ("E1", "E2", "E3") for s in ("dev", "heldout")]


class _NoOpIncident:
    """Delegates identity to a real incident but plants nothing (zero effect)."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def apply(
        self, tables: dict[str, pd.DataFrame], rng: np.random.Generator
    ) -> dict[str, pd.DataFrame]:
        return dict(tables)


@pytest.mark.unit
@pytest.mark.parametrize("case_id", ALL_IDS)
def test_applied_incident_is_visible(
    case_id: str, incidents120: dict[str, Any], applied120: Any
) -> None:
    from rootcause_evals.manufacturing.incidents import VisibilityReport, assert_visible

    report = assert_visible(applied120[case_id], incidents120[case_id])

    assert isinstance(report, VisibilityReport)
    assert report.z >= Z_MIN
    assert report.visible is True


@pytest.mark.unit
@pytest.mark.parametrize("case_id", ALL_IDS)
def test_baseline_without_the_incident_is_not_visible(
    case_id: str, incidents120: dict[str, Any], baseline120: Any
) -> None:
    from rootcause_evals.manufacturing.incidents import assert_visible

    report = assert_visible(baseline120, incidents120[case_id])

    assert report.z < Z_MIN
    assert report.visible is False


@pytest.mark.unit
def test_generate_raises_when_a_planted_incident_is_not_visible(
    config120: Any, incidents120: dict[str, Any]
) -> None:
    from rootcause_evals.manufacturing import generate
    from rootcause_evals.manufacturing.incidents import IncidentNotVisibleError

    weak = _NoOpIncident(incidents120["E1-dev"])

    with pytest.raises(IncidentNotVisibleError):
        generate(config120, incidents=(weak,))


@pytest.mark.unit
def test_incident_not_visible_error_is_an_exception() -> None:
    from rootcause_evals.manufacturing.incidents import IncidentNotVisibleError

    assert issubclass(IncidentNotVisibleError, Exception)


@pytest.mark.unit
def test_generate_defaults_to_the_plain_baseline(config120: Any) -> None:
    import numpy as np
    from pandas.testing import assert_frame_equal

    from rootcause_evals.manufacturing import generate
    from rootcause_evals.manufacturing.baseline import build_baseline
    from rootcause_evals.manufacturing.dimensions import build_dimensions

    rng = np.random.default_rng(config120.seed)
    dims = build_dimensions(config120, rng)
    expected = {**dims, **build_baseline(config120, rng, dims)}

    for result in (generate(config120), generate(config120, incidents=None)):
        assert set(result) == set(expected)
        for name, frame in expected.items():
            assert_frame_equal(result[name], frame)


@pytest.mark.unit
def test_generate_with_all_incidents_plants_every_case(
    config120: Any, baseline120: Any, incidents120: dict[str, Any]
) -> None:
    from rootcause_evals.manufacturing import generate
    from rootcause_evals.manufacturing.incidents import assert_visible, instantiate

    tables = generate(config120, incidents=instantiate(config120))

    assert len(tables) == 20
    assert not tables["production_runs"].equals(baseline120["production_runs"])
    outages = tables["business_events"].query("event_type == 'outage'")
    assert len(outages) == 2
    assert (tables["maintenance_events"]["parts_replaced"] == "coolant pump").sum() == 2
    for incident in incidents120.values():
        assert assert_visible(tables, incident).visible is True


@pytest.mark.unit
def test_generate_with_manifest_returns_tables_and_six_cases(config120: Any) -> None:
    from rootcause_evals.manufacturing import generate_with_manifest
    from rootcause_evals.manufacturing.incidents import CaseManifest

    tables, cases = generate_with_manifest(config120)

    assert len(tables) == 20
    assert len(cases) == 6
    assert all(isinstance(c, CaseManifest) for c in cases)
    assert sorted(c.case_id for c in cases) == sorted(ALL_IDS)


def _cases(path: Path) -> list[dict[str, Any]]:
    return json.loads((path / "cases.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _run(out: Path, incidents: str) -> int:
    from rootcause_evals.manufacturing.cli import main

    return main(
        ["generate", "--out", str(out), "--days", "120", "--seed", "7", "--incidents", incidents]
    )


@pytest.mark.unit
def test_cli_all_writes_six_cases_and_the_warehouse(tmp_path: Path) -> None:
    assert _run(tmp_path, "all") == 0

    cases = _cases(tmp_path)
    assert len(cases) == 6
    assert sorted(c["case_id"] for c in cases) == sorted(ALL_IDS)
    assert (tmp_path / "warehouse.duckdb").is_file()


@pytest.mark.unit
def test_cli_none_writes_no_cases(tmp_path: Path) -> None:
    assert _run(tmp_path, "none") == 0

    path = tmp_path / "cases.json"
    assert (not path.exists()) or _cases(tmp_path) == []


@pytest.mark.unit
def test_cli_selects_archetypes_by_id(tmp_path: Path) -> None:
    assert _run(tmp_path, "E1,E2") == 0

    cases = _cases(tmp_path)
    assert len(cases) == 4
    assert {c["archetype"] for c in cases} == {"E1", "E2"}


@pytest.mark.unit
def test_cli_rejects_an_unknown_archetype(tmp_path: Path) -> None:
    assert _run(tmp_path / "ok", "none") == 0
    try:
        code: int | str | None = _run(tmp_path, "ZZ")
    except SystemExit as exc:
        code = exc.code
    except (KeyError, ValueError):
        code = 2

    assert code not in (0, None)
