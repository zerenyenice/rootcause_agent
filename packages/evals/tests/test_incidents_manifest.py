"""Ground-truth manifest: PRD section 11 fields and the cases.json writer."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

ROOT_CAUSE_TYPES = {"process", "design", "data", "external", "equipment", "knowledge"}
EXPECTED = {
    "E1": {"direction": "up", "root_cause_type": "equipment"},
    "E2": {"direction": "down", "root_cause_type": "external"},
    "E3": {"direction": "up", "root_cause_type": "process"},
}


def _entry(incident: Any) -> dict[str, Any]:
    entry = incident.manifest_entry()
    assert type(entry).__name__ == "CaseManifest"
    result: dict[str, Any] = dataclasses.asdict(entry)
    return result


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value))


@pytest.mark.unit
def test_manifest_entry_is_a_frozen_dataclass(incidents120: dict[str, Any]) -> None:
    from rootcause_evals.manufacturing.incidents import CaseManifest

    entry = incidents120["E1-dev"].manifest_entry()

    assert isinstance(entry, CaseManifest)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(entry, "case_id", "x")  # noqa: B010


@pytest.mark.unit
def test_manifest_entries_identify_the_incident(incidents120: dict[str, Any]) -> None:
    for incident in incidents120.values():
        entry = _entry(incident)

        assert entry["case_id"] == incident.case_id
        assert entry["archetype"] == incident.archetype
        assert entry["level"] == "easy"
        assert entry["split"] == incident.split
        assert entry["anomaly_window"] == {
            "start": incident.window[0].isoformat(),
            "end": incident.window[1].isoformat(),
        }
        assert entry["segment_filters"] == dict(incident.segment)


@pytest.mark.unit
def test_manifest_entries_carry_every_prd_field(
    incidents120: dict[str, Any], baseline120: Any
) -> None:
    for incident in incidents120.values():
        entry = _entry(incident)

        for name in ("case_id", "archetype", "level", "split", "keywords"):
            assert entry[name], name
        assert {"name", "table", "expression", "grain"} <= set(entry["metric"])
        assert all(entry["metric"][k] for k in ("name", "table", "expression", "grain"))
        assert entry["metric"]["table"] in baseline120
        assert entry["segment_filters"]
        effect = entry["expected_effect"]
        assert effect["direction"] in {"up", "down"}
        assert isinstance(effect["size"], float)
        assert effect["size"] > 0
        assert entry["root_cause"]["statement"]
        assert entry["root_cause"]["type"] in ROOT_CAUSE_TYPES
        assert entry["counterfactual"]["description"]
        assert entry["decoys"] == []
        assert isinstance(entry["benign_events_in_window"], list)
        assert all(isinstance(k, str) and k for k in entry["keywords"])


@pytest.mark.unit
def test_manifest_chain_is_ordered_evidence_over_real_tables_and_columns(
    incidents120: dict[str, Any], baseline120: Any
) -> None:
    for incident in incidents120.values():
        chain = _entry(incident)["chain"]

        assert len(chain) >= 2
        for step in chain:
            assert step["cause"]
            assert step["evidence_tables"]
            assert isinstance(step["evidence_columns"], list)
            assert set(step["evidence_tables"]) <= set(baseline120)
            known = {c for t in step["evidence_tables"] for c in baseline120[t].columns}
            assert set(step["evidence_columns"]) <= known


@pytest.mark.unit
@pytest.mark.parametrize("archetype", sorted(EXPECTED))
def test_manifest_direction_and_root_cause_type_per_archetype(
    archetype: str, incidents120: dict[str, Any]
) -> None:
    for split in ("dev", "heldout"):
        entry = _entry(incidents120[f"{archetype}-{split}"])

        assert entry["expected_effect"]["direction"] == EXPECTED[archetype]["direction"]
        assert entry["root_cause"]["type"] == EXPECTED[archetype]["root_cause_type"]


@pytest.mark.unit
def test_manifest_names_the_planted_evidence(incidents120: dict[str, Any]) -> None:
    e1 = {t for s in _entry(incidents120["E1-dev"])["chain"] for t in s["evidence_tables"]}
    e2 = {t for s in _entry(incidents120["E2-dev"])["chain"] for t in s["evidence_tables"]}
    e3 = {t for s in _entry(incidents120["E3-dev"])["chain"] for t in s["evidence_tables"]}

    assert {"machine_telemetry", "maintenance_events"} <= e1
    assert {"downtime_events", "business_events"} <= e2
    assert "work_orders" in e3


@pytest.mark.unit
def test_write_cases_json_round_trips_and_sorts_keys(
    tmp_path: Path, incidents120: dict[str, Any]
) -> None:
    from rootcause_evals.manufacturing.incidents import write_cases_json

    cases = tuple(i.manifest_entry() for i in incidents120.values())
    path = tmp_path / "cases.json"

    write_cases_json(cases, path)

    seen: list[list[str]] = []

    def record(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen.append([k for k, _ in pairs])
        return dict(pairs)

    loaded = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=record)
    assert loaded == [_jsonable(dataclasses.asdict(c)) for c in cases]
    assert all(keys == sorted(keys) for keys in seen)


@pytest.mark.unit
def test_write_cases_json_is_byte_stable(tmp_path: Path, incidents120: dict[str, Any]) -> None:
    from rootcause_evals.manufacturing.incidents import write_cases_json

    cases = tuple(i.manifest_entry() for i in incidents120.values())

    write_cases_json(cases, tmp_path / "a.json")
    write_cases_json(cases, tmp_path / "b.json")

    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


@pytest.mark.unit
def test_write_cases_json_with_no_cases_writes_an_empty_list(tmp_path: Path) -> None:
    from rootcause_evals.manufacturing.incidents import write_cases_json

    write_cases_json((), tmp_path / "cases.json")

    assert json.loads((tmp_path / "cases.json").read_text(encoding="utf-8")) == []
