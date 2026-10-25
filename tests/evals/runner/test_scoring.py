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
from rootcause.evals.scoring import (
    NO_SCORABLE_KEYWORDS,
    CaseScore,
    hit_threshold,
    scorable_keywords,
    score_case,
)

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
        keyword_hits=4,
        keyword_total=4,
        root_cause_hit=True,
        passed=True,
        error=None,
    )


def _real_cases(incidents120: dict[str, Any]) -> list[CaseManifest]:
    return [incident.manifest_entry() for incident in incidents120.values()]


def test_the_real_easy_cases_cover_every_archetype_and_split(incidents120: dict[str, Any]) -> None:
    ids = {c.case_id for c in _real_cases(incidents120)}

    assert ids == {f"{a}-{s}" for a in ("E1", "E2", "E3") for s in ("dev", "heldout")}


def test_the_fake_agent_passes_every_real_easy_case(
    incidents120: dict[str, Any], valid_chain: Any
) -> None:
    for truth in _real_cases(incidents120):
        assert score_case(truth, valid_chain(truth)).passed, truth.case_id


def test_the_gaming_chain_fails_every_real_easy_case(
    incidents120: dict[str, Any], chains: Any
) -> None:
    for truth in _real_cases(incidents120):
        chain = chains.gaming(question_for(truth), truth)

        score = score_case(truth, chain)

        assert score.chain_valid, truth.case_id
        assert not score.root_cause_hit, (truth.case_id, score.keyword_hits)
        assert not score.passed, truth.case_id


def test_every_real_easy_case_has_scorable_keywords(incidents120: dict[str, Any]) -> None:
    for truth in _real_cases(incidents120):
        question = question_for(truth).casefold()

        remaining = scorable_keywords(truth)

        assert remaining, truth.case_id
        assert all(keyword.casefold() not in question for keyword in remaining)


def test_an_invalid_chain_fails_even_with_the_right_cause(case: CaseManifest, chains: Any) -> None:
    score = score_case(case, chains.invalid(case))

    assert score.root_cause_hit
    assert not score.chain_valid
    assert score.issues == ("level_without_evidence",)
    assert not score.passed


def test_a_wrong_cause_fails(case: CaseManifest, chains: Any) -> None:
    score = score_case(case, chains.wrong_cause(case))

    assert score.chain_valid
    assert score.keyword_hits == 0
    assert not score.root_cause_hit
    assert not score.passed


def test_a_missing_chain_fails_without_issues(case: CaseManifest) -> None:
    score = score_case(case, None)

    assert not score.passed
    assert not score.chain_valid
    assert score.keyword_total == len(scorable_keywords(case))
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


def _bare_chain(case: CaseManifest, valid_chain: Any, statement: str, *answers: str) -> WhyChain:
    """A valid-shaped chain whose root cause and answers are exactly the given texts."""
    root = valid_chain(case).root_cause.model_copy(update={"statement": statement})
    levels = tuple(
        WhyLevel(
            question="Why?",
            answer=answer,
            evidence=(Evidence(sql="SELECT 1", rows_summary=""),),
            confidence=0.5,
        )
        for answer in answers
    )
    return _chain_with(case, valid_chain, root_cause=root, levels=levels)


def test_keywords_in_evidence_sql_do_not_count(case_factory: Any, valid_chain: Any) -> None:
    case = case_factory(keywords=("maintenance_events", "M005"))
    sql_only = WhyLevel(
        question="Why?",
        answer="Something happened",
        evidence=(
            Evidence(sql="SELECT * FROM maintenance_events WHERE m = 'M005'", rows_summary=""),
        ),
        confidence=0.5,
    )
    chain = _chain_with(
        case,
        valid_chain,
        root_cause=valid_chain(case).root_cause.model_copy(update={"statement": "Unclear"}),
        levels=(sql_only,) * 3,
    )

    assert score_case(case, chain).keyword_hits == 0


def test_keywords_in_branch_answers_and_root_causes_count(
    case_factory: Any, valid_chain: Any
) -> None:
    case = case_factory(keywords=("M005", "coolant pump"))
    trunk = _bare_chain(case, valid_chain, "Unclear", "Nothing found")
    branch = _bare_chain(case, valid_chain, "The coolant pump failed", "Machine M005 ran hot")

    assert score_case(case, trunk).keyword_hits == 0
    assert score_case(case, trunk.model_copy(update={"branches": (branch,)})).keyword_hits == 2


def test_keywords_given_away_by_the_question_are_not_scored(
    case: CaseManifest, valid_chain: Any
) -> None:
    question = question_for(case).casefold()

    remaining = scorable_keywords(case)

    assert remaining == ("coolant pump", "temperature_c", "maintenance_events", "M005")
    assert "scrap" in question and "l03" in question
    assert score_case(case, valid_chain(case)).keyword_total == len(remaining)


def test_giveaway_removal_is_case_insensitive(case_factory: Any) -> None:
    case = case_factory(keywords=("SCRAP_RATE", "l03", "M005"))

    assert scorable_keywords(case) == ("M005",)


@pytest.mark.parametrize(
    ("keyword", "text", "hit"),
    [
        ("day", "the plant stopped today", False),
        ("day", "the plant stopped for the day shift", True),
        ("day", "the day_shift table", False),
        ("downtime_events", "rows in downtime_events show it", True),
        ("downtime_events", "rows in downtime_events_v2 show it", False),
        ("downtime_events", "rows in old_downtime_events show it", False),
        ("downtime_events", "downtime events were logged", False),
        ("product family", "the new Product  Family is slower", True),
        ("product family", "the product familyA is slower", False),
        ("M005", "machine m005, again", True),
        ("COOLANT PUMP", "the coolant pump failed", True),
        ("a.b", "axb", False),
    ],
)
def test_keywords_match_whole_words_and_phrases(
    keyword: str, text: str, hit: bool, case_factory: Any, valid_chain: Any
) -> None:
    case = case_factory(keywords=(keyword,))
    chain = _bare_chain(case, valid_chain, text, "Nothing else", "Nothing else", "Nothing else")

    assert score_case(case, chain).keyword_hits == int(hit)


@pytest.mark.parametrize(("total", "needed"), [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (0, 1)])
def test_hit_threshold_is_half_rounded_up_and_at_least_one(total: int, needed: int) -> None:
    assert hit_threshold(total) == needed


def test_root_cause_needs_half_the_scorable_keywords_rounded_up(
    case_factory: Any, valid_chain: Any
) -> None:
    present = ("coolant pump", "M005", "temperature_c")
    absent = ("zz1", "zz2")
    enough = case_factory(keywords=(*present, *absent, "scrap"))
    short = case_factory(keywords=(*present[:2], *absent, "zz3", "L03"))

    enough_score = score_case(enough, valid_chain(enough))
    short_score = score_case(short, valid_chain(short))

    assert (enough_score.keyword_hits, enough_score.keyword_total) == (3, 5)
    assert enough_score.root_cause_hit
    assert (short_score.keyword_hits, short_score.keyword_total) == (2, 5)
    assert not short_score.root_cause_hit


@pytest.mark.parametrize(
    "keywords", [(), ("scrap", "L03"), ("SCRAP_rate", "2025-03-03"), ("", "  ")]
)
def test_a_case_without_scorable_keywords_fails(
    keywords: tuple[str, ...], case_factory: Any, valid_chain: Any
) -> None:
    case = case_factory(keywords=keywords)

    score = score_case(case, valid_chain(case))

    assert not score.passed
    assert not score.root_cause_hit
    assert score.error == NO_SCORABLE_KEYWORDS
    assert score.keyword_total == 0


def test_no_scorable_keywords_wins_over_a_missing_chain(case_factory: Any) -> None:
    case = case_factory(keywords=("scrap",))

    assert score_case(case, None).error == NO_SCORABLE_KEYWORDS
    assert score_case(case, None, error="RuntimeError").error == NO_SCORABLE_KEYWORDS


def test_no_root_cause_means_no_hit(case: CaseManifest, valid_chain: Any) -> None:
    chain = _chain_with(case, valid_chain, root_cause=None, partial=True)

    score = score_case(case, chain)

    assert score.chain_valid
    assert score.keyword_hits >= hit_threshold(score.keyword_total)
    assert not score.root_cause_hit
    assert not score.passed


def test_case_score_is_frozen(case: CaseManifest) -> None:
    score = score_case(case, None)

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(score, "passed", True)  # noqa: B010
