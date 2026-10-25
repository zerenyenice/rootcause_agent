"""Evaluation runner (#45): the analyst question and per-case scoring."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from rootcause.agents.reports import Evidence, WhyChain, WhyLevel
from rootcause.evals.manufacturing.incidents.manifest import (
    AnomalyWindow,
    CaseManifest,
    ExpectedEffect,
    FrozenDict,
)
from rootcause.evals.runner import question_for
from rootcause.evals.scoring import CaseScore, score_case

pytestmark = pytest.mark.unit


def _leakable_keywords(case: CaseManifest) -> list[str]:
    """Keywords the question may not contain: all but the metric name and segment values."""
    allowed = (case.metric.name.casefold(), *(v.casefold() for v in case.segment_filters.values()))
    return [k for k in case.keywords if not any(k.casefold() in text for text in allowed)]


def _assert_no_leak(case: CaseManifest) -> None:
    question = question_for(case).casefold()
    for keyword in _leakable_keywords(case):
        assert keyword.casefold() not in question, keyword
    secrets = [
        case.root_cause.statement,
        case.counterfactual.description,
        case.counterfactual.sql_hint,
        *(step.cause for step in case.chain),
        *(step.sql_hint for step in case.chain),
        *(decoy.description for decoy in case.decoys),
        *case.benign_events_in_window,
    ]
    for secret in secrets:
        assert secret.casefold() not in question, secret


def test_question_matches_the_documented_example(case: CaseManifest) -> None:
    assert question_for(case) == (
        "Why did scrap_rate go up by about 60% between 2025-03-03 and 2025-03-09 for line L03?"
    )


def test_question_for_a_single_day_drop(case_factory: Any) -> None:
    case = case_factory(
        anomaly_window=AnomalyWindow(start="2025-11-28", end="2025-11-28"),
        expected_effect=ExpectedEffect(direction="down", size=1.0),
        segment_filters=FrozenDict({"plant_id": "P02"}),
    )

    assert question_for(case).endswith("go down by about 100% on 2025-11-28 for plant P02?")


def test_question_joins_sorted_segments_and_omits_empty_ones(case_factory: Any) -> None:
    both = case_factory(segment_filters=FrozenDict({"plant_id": "P01", "line_id": "L02"}))
    none = case_factory(segment_filters=FrozenDict({}))

    assert question_for(both).endswith("for line L02 and plant P01?")
    assert question_for(none).endswith("and 2025-03-09?")


def test_question_rounds_the_effect_size(case_factory: Any) -> None:
    case = case_factory(expected_effect=ExpectedEffect(direction="up", size=0.278))

    assert "by about 28%" in question_for(case)


def test_question_never_leaks_the_synthetic_ground_truth(case: CaseManifest) -> None:
    _assert_no_leak(case)


def test_question_never_leaks_the_real_easy_cases(incidents120: dict[str, Any]) -> None:
    cases = [incident.manifest_entry() for incident in incidents120.values()]

    assert {c.archetype for c in cases} == {"E1", "E2", "E3"}
    for real in cases:
        _assert_no_leak(real)


def test_a_valid_chain_restating_the_truth_passes(case: CaseManifest, valid_chain: Any) -> None:
    score = score_case(case, valid_chain(case))

    assert score == CaseScore(
        case_id="E1-test",
        level="easy",
        chain_valid=True,
        issues=(),
        keyword_hits=6,
        keyword_total=6,
        root_cause_hit=True,
        passed=True,
        error=None,
    )


def test_the_fake_agent_passes_every_real_easy_case(
    incidents120: dict[str, Any], valid_chain: Any
) -> None:
    for incident in incidents120.values():
        truth = incident.manifest_entry()

        assert score_case(truth, valid_chain(truth)).passed, truth.case_id


def test_an_invalid_chain_fails_even_with_the_right_cause(case: CaseManifest, agents: Any) -> None:
    score = score_case(case, agents.invalid("q", case))

    assert score.root_cause_hit
    assert not score.chain_valid
    assert score.issues == ("level_without_evidence",)
    assert not score.passed


def test_a_wrong_cause_fails(case: CaseManifest, agents: Any) -> None:
    score = score_case(case, agents.wrong_cause("q", case))

    assert score.chain_valid
    assert score.keyword_hits == 0
    assert not score.root_cause_hit
    assert not score.passed


def test_a_missing_chain_fails_without_issues(case: CaseManifest) -> None:
    score = score_case(case, None)

    assert not score.passed
    assert not score.chain_valid
    assert score.keyword_total == len(case.keywords)
    assert score.error is None


def test_an_error_fails_even_with_a_valid_chain(case: CaseManifest, valid_chain: Any) -> None:
    score = score_case(case, valid_chain(case), error="TimeoutError")

    assert not score.passed
    assert score.error == "TimeoutError"
    assert score.keyword_hits == 0


def _chain_with(case: CaseManifest, valid_chain: Any, **update: Any) -> WhyChain:
    chain: WhyChain = valid_chain(case)
    return chain.model_copy(update=update)


def test_keywords_match_case_insensitively(case_factory: Any, valid_chain: Any) -> None:
    case = case_factory(keywords=("COOLANT PUMP", "m005"))

    assert score_case(case, valid_chain(case)).keyword_hits == 2


def test_keywords_in_evidence_sql_and_branches_count(case: CaseManifest, valid_chain: Any) -> None:
    plain = case.root_cause.statement.replace("M005", "it")
    root = valid_chain(case).root_cause.model_copy(update={"statement": plain})
    branch_level = WhyLevel(
        question="Why?",
        answer="Branch finding",
        evidence=(Evidence(sql="SELECT * FROM t WHERE machine_id = 'M005'", rows_summary=""),),
        confidence=0.5,
    )
    branch = _chain_with(case, valid_chain, levels=(branch_level,) * 3)
    no_m005 = dataclasses.replace(case, keywords=("M005",))
    without = _chain_with(case, valid_chain, root_cause=root, levels=())

    assert score_case(no_m005, without).keyword_hits == 0
    assert score_case(no_m005, without.model_copy(update={"branches": (branch,)})).keyword_hits == 1


def test_root_cause_needs_half_the_keywords_rounded_up(case_factory: Any, valid_chain: Any) -> None:
    present = ("coolant pump", "M005", "L03")
    absent = ("zz1", "zz2")
    enough = case_factory(keywords=(*present, *absent))
    short = case_factory(keywords=(*present[:2], *absent, "zz3"))

    assert score_case(enough, valid_chain(enough)).root_cause_hit  # 3 of 5
    assert not score_case(short, valid_chain(short)).root_cause_hit  # 2 of 5


def test_no_root_cause_means_no_hit(case: CaseManifest, valid_chain: Any) -> None:
    chain = _chain_with(case, valid_chain, root_cause=None, partial=True)

    score = score_case(case, chain)

    assert score.chain_valid
    assert score.keyword_hits >= 3
    assert not score.root_cause_hit
    assert not score.passed


def test_case_score_is_frozen(case: CaseManifest) -> None:
    score = score_case(case, None)

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(score, "passed", True)  # noqa: B010
