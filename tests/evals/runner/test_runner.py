"""Evaluation runner (#45): running cases, pass rates and the JSON/markdown report."""

from __future__ import annotations

import json
from typing import Any

import pytest

from rootcause.evals.manufacturing.incidents.manifest import CaseManifest
from rootcause.evals.runner import LevelPassRate, question_for, run_cases

pytestmark = pytest.mark.unit


def test_a_valid_agent_passes_every_case(case_factory: Any, agents: Any) -> None:
    cases = [case_factory(case_id="E1-a"), case_factory(case_id="E2-a")]

    report = run_cases(cases, agents.valid(cases))

    assert report.all_passed
    assert [s.case_id for s in report.scores] == ["E1-a", "E2-a"]
    assert report.pass_rates == (LevelPassRate(level="easy", passed=2, total=2),)
    assert report.pass_rates[0].rate == 1.0


def test_the_agent_receives_the_question_only(case: CaseManifest, agents: Any) -> None:
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    answer = agents.valid([case])

    def investigate(*args: Any, **kwargs: Any) -> Any:
        seen.append((args, kwargs))
        return answer(*args, **kwargs)

    report = run_cases([case], investigate)

    assert seen == [((question_for(case),), {})]
    assert seen[0][0][0].startswith("Why did scrap_rate go up")
    assert report.all_passed


def test_no_manifest_field_reaches_the_agent(case: CaseManifest) -> None:
    received: list[Any] = []

    def investigate(*args: Any, **kwargs: Any) -> None:
        received.extend([*args, *kwargs.values()])

    run_cases([case], investigate)

    assert all(isinstance(arg, str) for arg in received)
    assert not any(isinstance(arg, CaseManifest) for arg in received)
    assert case.root_cause.statement not in " ".join(received)


def test_an_exception_fails_the_case_with_its_type_name_only(
    case_factory: Any, agents: Any
) -> None:
    cases = [case_factory(case_id="a"), case_factory(case_id="b")]
    calls = iter([agents.raising(cases), agents.valid(cases)])

    def investigate(question: str) -> Any:
        return next(calls)(question)

    report = run_cases(cases, investigate)

    failed, passed = report.scores
    assert failed.error == "RuntimeError"
    assert not failed.passed
    assert passed.passed
    assert "crashed" not in report.to_json()
    assert "crashed" not in report.to_markdown()


def test_none_and_non_chain_outputs_fail(case: CaseManifest, agents: Any) -> None:
    none = run_cases([case], agents.none([case])).scores[0]
    not_a_chain: Any = lambda q: {"root_cause": "x"}  # noqa: E731
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

    report = run_cases(cases, agents.valid(cases))

    assert not report.all_passed
    assert report.pass_rates == (
        LevelPassRate(level="medium", passed=1, total=1),
        LevelPassRate(level="easy", passed=1, total=2),
    )
    assert report.pass_rates[1].rate == 0.5


def test_an_empty_run_does_not_count_as_passed(agents: Any) -> None:
    report = run_cases([], agents.valid([]))

    assert not report.all_passed
    assert report.pass_rates == ()
    assert LevelPassRate(level="easy", passed=0, total=0).rate == 0.0


def test_to_json_holds_scores_rates_and_verdict(case_factory: Any, agents: Any) -> None:
    cases = [case_factory()]
    report = run_cases(cases, agents.invalid(cases))

    payload = json.loads(report.to_json())

    assert payload["all_passed"] is False
    assert payload["scores"][0]["case_id"] == "E1-test"
    assert payload["scores"][0]["issues"] == ["level_without_evidence"]
    assert payload["scores"][0]["chain_valid"] is False
    assert payload["pass_rates"] == [{"level": "easy", "passed": 0, "total": 1, "rate": 0.0}]


def test_to_markdown_has_a_row_per_case_then_pass_rates(case_factory: Any, agents: Any) -> None:
    cases = [case_factory(case_id="E1|dev"), case_factory(case_id="E3-dev")]

    markdown = run_cases(cases, agents.wrong_cause(cases)).to_markdown()

    case_table, rates = markdown.split("## Pass rates")
    assert "| Scored keywords |" in case_table
    assert "| E1\\|dev | easy | no | yes | 0/4 | no | - | - |" in case_table
    assert "| E3-dev | easy | no |" in case_table
    assert "| easy | 0 | 2 | 0% |" in rates


def test_to_markdown_lists_issues_and_errors(case: CaseManifest, agents: Any) -> None:
    invalid = run_cases([case], agents.invalid([case])).to_markdown()
    raising = run_cases([case], agents.raising([case])).to_markdown()

    assert "| level_without_evidence | - |" in invalid
    assert "| - | RuntimeError |" in raising
