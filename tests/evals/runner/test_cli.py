"""Evaluation runner (#45): cases.json round-trip and the ``rootcause.evals.run`` CLI."""

from __future__ import annotations

import dataclasses
import importlib
import json
import sys
import types
from typing import TYPE_CHECKING, Any

import pytest

from rootcause.evals.manufacturing.incidents import read_cases_json, write_cases_json
from rootcause.evals.manufacturing.incidents.manifest import FrozenDict
from rootcause.evals.run import EXIT_FAILED, EXIT_PASSED, EXIT_USAGE, main
from rootcause.evals.runner import load_cases, question_for

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


def _agent(agents: Any, name: str, cases_file: Path) -> Any:
    """A fake agent whose answer key is the cases file, closed over by the test."""
    factory = getattr(agents, name)
    return factory(read_cases_json(cases_file))


def _run(cases: Path, agent: str, *extra: str) -> int:
    return main(["--cases", str(cases), "--agent", agent, *extra])


def test_cli_exits_zero_and_writes_the_report_when_all_pass(
    cases_file: Path, tmp_path: Path, agents: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    agent = _install_agent(monkeypatch, _agent(agents, "valid", cases_file))
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
    agent = _install_agent(monkeypatch, _agent(agents, name, cases_file))

    assert _run(cases_file, agent, "--levels", "easy") == EXIT_FAILED


def test_cli_runs_every_level_by_default(
    cases_file: Path, agents: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    agent = _install_agent(monkeypatch, _agent(agents, "valid", cases_file))

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
        (f"{FAKE_MODULE}:investigate", "all,easy"),
        (f"{FAKE_MODULE}:investigate", "easy, all"),
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
    _install_agent(monkeypatch, _agent(agents, "valid", cases_file))

    assert _run(cases_file, agent, "--levels", levels) == EXIT_USAGE
    assert capsys.readouterr().err.startswith("error:")


def test_cli_missing_or_malformed_cases_file_exits_two(
    tmp_path: Path, agents: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _install_agent(monkeypatch, agents.valid([]))
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")

    assert _run(tmp_path / "absent.json", agent) == EXIT_USAGE
    assert _run(broken, agent) == EXIT_USAGE


def test_cli_missing_arguments_exit_two() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--cases", "cases.json"])

    assert exc.value.code == EXIT_USAGE


def test_the_cli_agent_receives_the_question_only(
    cases_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def investigate(*args: Any, **kwargs: Any) -> None:
        received.append((args, kwargs))

    _run(cases_file, _install_agent(monkeypatch, investigate), "--levels", "hard")

    (hard,) = [c for c in read_cases_json(cases_file) if c.level == "hard"]
    assert received == [((question_for(hard),), {})]


def test_cli_all_alone_still_selects_every_level(
    cases_file: Path, agents: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    agent = _install_agent(monkeypatch, _agent(agents, "valid", cases_file))

    assert _run(cases_file, agent, "--levels", " all ") == EXIT_PASSED
    assert "| hard | 1 | 1 | 100% |" in capsys.readouterr().out


def test_cli_all_with_other_levels_names_the_problem(
    cases_file: Path, agents: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    agent = _install_agent(monkeypatch, _agent(agents, "valid", cases_file))

    assert _run(cases_file, agent, "--levels", "all,easy") == EXIT_USAGE
    assert "cannot be combined" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error",
    [RuntimeError("boom at import"), SyntaxError("bad syntax"), ValueError("bad config")],
    ids=["runtime", "syntax", "value"],
)
def test_cli_any_exception_importing_the_agent_exits_two(
    error: Exception, cases_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    def failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(importlib, "import_module", failing_import)

    assert _run(cases_file, "broken_agent_module:investigate", "--levels", "easy") == EXIT_USAGE
    err = capsys.readouterr().err
    assert err.startswith("error: cannot import agent module 'broken_agent_module'")
    assert type(error).__name__ in err


def test_cli_a_real_module_raising_at_import_exits_two(
    cases_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    package = tmp_path / "agents_pkg"
    package.mkdir()
    (package / "exploding_agent_xyz.py").write_text(
        "raise RuntimeError('exploded at import')\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(package))

    assert _run(cases_file, "exploding_agent_xyz:investigate") == EXIT_USAGE
    assert "RuntimeError" in capsys.readouterr().err


def test_cli_unwritable_out_exits_two_after_printing_the_report(
    cases_file: Path, tmp_path: Path, agents: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    agent = _install_agent(monkeypatch, _agent(agents, "valid", cases_file))
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("", encoding="utf-8")

    code = _run(cases_file, agent, "--out", str(blocker / "report.json"))

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert captured.err.startswith("error: cannot write report to")
    assert "# Evaluation report" in captured.out


def test_cli_out_pointing_at_a_directory_exits_two(
    cases_file: Path, tmp_path: Path, agents: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    agent = _install_agent(monkeypatch, _agent(agents, "valid", cases_file))

    assert _run(cases_file, agent, "--out", str(tmp_path)) == EXIT_USAGE
    assert "cannot write report" in capsys.readouterr().err
