"""Evaluation runner (#45): running cases, pass rates and the JSON/markdown report."""

from __future__ import annotations

import json
from typing import Any

import pytest

from rootcause.evals.manufacturing.incidents.manifest import CaseManifest
from rootcause.evals.runner import LevelPassRate, run_cases

pytestmark = pytest.mark.unit


def test_a_valid_agent_passes_every_case(case_factory: Any, agents: Any) -> None:
    cases = [case_factory(case_id="E1-a"), case_factory(case_id="E2-a")]

    report = run_cases(cases, agents.valid)

    assert report.all_passed
    assert [s.case_id for s in report.scores] == ["E1-a", "E2-a"]
    assert report.pass_rates == (LevelPassRate(level="easy", passed=2, total=2),)
    assert report.pass_rates[0].rate == 1.0


def test_the_agent_receives_the_question_and_the_case(case: CaseManifest, agents: Any) -> None:
    seen: list[tuple[str, str]] = []

    def investigate(question: str, received: CaseManifest) -> Any:
        seen.append((question, received.case_id))
        return agents.valid(question, received)

    run_cases([case], investigate)

    assert seen == [(seen[0][0], "E1-test")]
    assert seen[0][0].startswith("Why did scrap_rate go up")


def test_an_exception_fails_the_case_with_its_type_name_only(
    case_factory: Any, agents: Any
) -> None:
    calls = iter([agents.raising, agents.valid])

    def investigate(question: str, case: CaseManifest) -> Any:
        return next(calls)(question, case)

    report = run_cases([case_factory(case_id="a"), case_factory(case_id="b")], investigate)

    failed, passed = report.scores
    assert failed.error == "RuntimeError"
    assert not failed.passed
    assert passed.passed
    assert "crashed" not in report.to_json()
    assert "crashed" not in report.to_markdown()


def test_none_and_non_chain_outputs_fail(case: CaseManifest, agents: Any) -> None:
    none = run_cases([case], agents.none).scores[0]
    not_a_chain: Any = lambda q, c: {"root_cause": "x"}  # noqa: E731
    wrong_type = run_cases([case], not_a_chain).scores[0]

    assert not none.passed
    assert none.error is None
    assert not wrong_type.passed
    assert wrong_type.error == "TypeError"


def test_pass_rates_are_per_level_in_first_seen_order(case_factory: Any, agents: Any) -> None:
    cases = [
        case_factory(case_id="m1", level="medium"),
        case_factory(case_id="e1"),
        case_factory(case_id="e2", keywords=("absent-1", "absent-2")),
    ]

    report = run_cases(cases, agents.valid)

    assert not report.all_passed
    assert report.pass_rates == (
        LevelPassRate(level="medium", passed=1, total=1),
        LevelPassRate(level="easy", passed=1, total=2),
    )
    assert report.pass_rates[1].rate == 0.5


def test_an_empty_run_does_not_count_as_passed(agents: Any) -> None:
    report = run_cases([], agents.valid)

    assert not report.all_passed
    assert report.pass_rates == ()
    assert LevelPassRate(level="easy", passed=0, total=0).rate == 0.0


def test_to_json_holds_scores_rates_and_verdict(case_factory: Any, agents: Any) -> None:
    report = run_cases([case_factory()], agents.invalid)

    payload = json.loads(report.to_json())

    assert payload["all_passed"] is False
    assert payload["scores"][0]["case_id"] == "E1-test"
    assert payload["scores"][0]["issues"] == ["level_without_evidence"]
    assert payload["scores"][0]["chain_valid"] is False
    assert payload["pass_rates"] == [{"level": "easy", "passed": 0, "total": 1, "rate": 0.0}]


def test_to_markdown_has_a_row_per_case_then_pass_rates(case_factory: Any, agents: Any) -> None:
    cases = [case_factory(case_id="E1|dev"), case_factory(case_id="E3-dev")]

    markdown = run_cases(cases, agents.wrong_cause).to_markdown()

    case_table, rates = markdown.split("## Pass rates")
    assert "| E1\\|dev | easy | no | yes | 0/6 | no | - | - |" in case_table
    assert "| E3-dev | easy | no |" in case_table
    assert "| easy | 0 | 2 | 0% |" in rates


def test_to_markdown_lists_issues_and_errors(case: CaseManifest, agents: Any) -> None:
    invalid = run_cases([case], agents.invalid).to_markdown()
    raising = run_cases([case], agents.raising).to_markdown()

    assert "| level_without_evidence | - |" in invalid
    assert "| - | RuntimeError |" in raising
