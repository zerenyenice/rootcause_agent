"""Evaluation runner (#45): cases.json round-trip and the ``rootcause.evals.run`` CLI."""

from __future__ import annotations

import dataclasses
import json
import sys
import types
from typing import TYPE_CHECKING, Any

import pytest

from rootcause.evals.manufacturing.incidents import read_cases_json, write_cases_json
from rootcause.evals.manufacturing.incidents.manifest import CaseManifest, FrozenDict
from rootcause.evals.run import EXIT_FAILED, EXIT_PASSED, EXIT_USAGE, main
from rootcause.evals.runner import load_cases

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

FAKE_MODULE = "fake_eval_agent_for_tests"


def test_load_cases_round_trips_synthetic_cases(case_factory: Any, tmp_path: Path) -> None:
    cases = (case_factory(), case_factory(case_id="E3-x", level="medium", decoys=()))
    path = tmp_path / "out" / "cases.json"
    write_cases_json(cases, path)

    loaded = load_cases(path)

    assert loaded == cases
    assert isinstance(loaded[0].segment_filters, FrozenDict)
    assert isinstance(loaded[0].chain[0].evidence_tables, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(loaded[0], "level", "hard")  # noqa: B010
    with pytest.raises(TypeError):
        filters: Any = loaded[0].segment_filters
        filters["line_id"] = "L09"


def test_load_cases_round_trips_the_real_easy_cases(
    incidents120: dict[str, Any], tmp_path: Path
) -> None:
    cases = tuple(incident.manifest_entry() for incident in incidents120.values())
    path = tmp_path / "cases.json"
    write_cases_json(cases, path)

    assert read_cases_json(path) == cases
    write_cases_json(read_cases_json(path), tmp_path / "again.json")
    assert (tmp_path / "again.json").read_bytes() == path.read_bytes()


@pytest.mark.parametrize(
    "content",
    ['{"case_id": "x"}', "[1]", '[{"case_id": "x"}]', "not json"],
    ids=["not-a-list", "not-an-object", "missing-fields", "not-json"],
)
def test_load_cases_rejects_malformed_files(content: str, tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_cases(path)


def _install_agent(monkeypatch: pytest.MonkeyPatch, agent: Any) -> str:
    module = types.ModuleType(FAKE_MODULE)
    module.investigate = agent  # type: ignore[attr-defined]
    module.not_callable = 42  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, FAKE_MODULE, module)
    return f"{FAKE_MODULE}:investigate"


@pytest.fixture
def cases_file(case_factory: Any, tmp_path: Path) -> Path:
    path = tmp_path / "cases.json"
    write_cases_json(
        [case_factory(case_id="E1-dev"), case_factory(case_id="H1-dev", level="hard")], path
    )
    return path


def _run(cases: Path, agent: str, *extra: str) -> int:
    return main(["--cases", str(cases), "--agent", agent, *extra])


def test_cli_exits_zero_and_writes_the_report_when_all_pass(
    cases_file: Path, tmp_path: Path, agents: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    agent = _install_agent(monkeypatch, agents.valid)
    out = tmp_path / "reports" / "report.json"

    code = _run(cases_file, agent, "--levels", "easy", "--out", str(out))

    assert code == EXIT_PASSED
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [s["case_id"] for s in payload["scores"]] == ["E1-dev"]
    assert payload["all_passed"] is True
    assert "| E1-dev | easy | yes |" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["invalid", "wrong_cause", "raising", "none"])
def test_cli_exits_one_when_a_case_fails(
    name: str, cases_file: Path, agents: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _install_agent(monkeypatch, getattr(agents, name))

    assert _run(cases_file, agent, "--levels", "easy") == EXIT_FAILED


def test_cli_runs_every_level_by_default(
    cases_file: Path, agents: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    agent = _install_agent(monkeypatch, agents.valid)

    assert _run(cases_file, agent) == EXIT_PASSED
    output = capsys.readouterr().out
    assert "| easy | 1 | 1 | 100% |" in output
    assert "| hard | 1 | 1 | 100% |" in output


@pytest.mark.parametrize(
    ("agent", "levels"),
    [
        ("no-colon", "easy"),
        (":investigate", "easy"),
        ("definitely_missing_module_xyz:investigate", "easy"),
        (f"{FAKE_MODULE}:missing", "easy"),
        (f"{FAKE_MODULE}:not_callable", "easy"),
        (f"{FAKE_MODULE}:investigate", "eassy"),
        (f"{FAKE_MODULE}:investigate", " , "),
    ],
)
def test_cli_usage_errors_exit_two(
    agent: str,
    levels: str,
    cases_file: Path,
    agents: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    _install_agent(monkeypatch, agents.valid)

    assert _run(cases_file, agent, "--levels", levels) == EXIT_USAGE
    assert capsys.readouterr().err.startswith("error:")


def test_cli_missing_or_malformed_cases_file_exits_two(
    tmp_path: Path, agents: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _install_agent(monkeypatch, agents.valid)
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")

    assert _run(tmp_path / "absent.json", agent) == EXIT_USAGE
    assert _run(broken, agent) == EXIT_USAGE


def test_cli_missing_arguments_exit_two() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--cases", "cases.json"])

    assert exc.value.code == EXIT_USAGE


def test_loaded_manifests_feed_the_agent(cases_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[CaseManifest] = []

    def investigate(question: str, case: CaseManifest) -> None:
        received.append(case)

    _run(cases_file, _install_agent(monkeypatch, investigate), "--levels", "hard")

    assert [c.case_id for c in received] == ["H1-dev"]
