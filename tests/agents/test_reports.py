"""WhyChain contract (issue #35): models, round-trip, and business-rule checks."""

from typing import Any

import pytest
from pydantic import ValidationError
from rootcause.agents.reports import (  # type: ignore[import-not-found]
    Countermeasure,
    Evidence,
    ProblemStatement,
    RootCause,
    ValidationCheck,
    WhyChain,
    WhyLevel,
    chain_issues,
)


def _any(value: str) -> Any:
    return value


def _problem() -> ProblemStatement:
    return ProblemStatement(what="Scrap up", when="Week 12", where="Line 3", impact="4% yield")


def _evidence(sql: str = "SELECT 1") -> Evidence:
    return Evidence(sql=sql, rows_summary="1 row")


def _level(n: int = 1, evidence: tuple[Evidence, ...] | None = None) -> WhyLevel:
    return WhyLevel(
        question=f"Why {n}?",
        answer=f"Because {n}",
        evidence=(_evidence(),) if evidence is None else evidence,
        confidence=0.8,
    )


def _root() -> RootCause:
    return RootCause(statement="No SOP", type="process", confidence=0.7)


def _check(passed: bool = True) -> ValidationCheck:
    return ValidationCheck(link="Why 2 -> Why 1", check_sql="SELECT 1", passed=passed)


def _chain(**overrides: Any) -> WhyChain:
    base: dict[str, Any] = {
        "problem": _problem(),
        "levels": tuple(_level(i) for i in range(1, 4)),
        "root_cause": _root(),
        "validation": (_check(),),
    }
    return WhyChain(**{**base, **overrides})


def test_valid_full_chain_has_no_issues() -> None:
    assert chain_issues(_chain()) == ()


def test_chain_round_trips_through_json() -> None:
    chain = _chain(
        countermeasures=(Countermeasure(type="preventive", action="Add SOP", owner="QA"),),
        branches=(_chain(branches=()),),
        notes="note ☃ \U0001f600",
    )
    assert WhyChain.model_validate_json(chain.model_dump_json()) == chain


def test_models_are_frozen() -> None:
    with pytest.raises(ValidationError):
        setattr(_problem(), "what", "other")  # noqa: B010
    with pytest.raises(ValidationError):
        setattr(_chain(), "partial", True)  # noqa: B010


@pytest.mark.parametrize("field", ["what", "when", "where", "impact"])
def test_problem_statement_rejects_empty_fields(field: str) -> None:
    data = {"what": "a", "when": "b", "where": "c", "impact": "d", field: ""}
    with pytest.raises(ValidationError):
        ProblemStatement(**data)


def test_evidence_requires_sql_and_defaults_result_path() -> None:
    with pytest.raises(ValidationError):
        Evidence(sql="", rows_summary="x")
    assert _evidence().result_path is None


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 5.0])
def test_confidence_out_of_range_is_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError):
        WhyLevel(question="q", answer="a", evidence=(_evidence(),), confidence=confidence)
    with pytest.raises(ValidationError):
        RootCause(statement="s", type="data", confidence=confidence)


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_confidence_boundaries_are_accepted(confidence: float) -> None:
    assert RootCause(statement="s", type="data", confidence=confidence).confidence == confidence


def test_invalid_root_cause_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RootCause(statement="s", type=_any("people"), confidence=0.5)


@pytest.mark.parametrize("kind", ["process", "design", "data", "external", "knowledge"])
def test_all_root_cause_types_are_accepted(kind: Any) -> None:
    assert RootCause(statement="s", type=kind, confidence=0.5).type == kind


def test_invalid_countermeasure_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Countermeasure(type=_any("punitive"), action="x")


def test_optional_defaults() -> None:
    chain = WhyChain(problem=_problem(), levels=(), root_cause=None)
    assert chain.validation == ()
    assert chain.countermeasures == ()
    assert chain.branches == ()
    assert chain.partial is False
    assert chain.notes == ""


def test_level_without_evidence_is_flagged() -> None:
    levels = (_level(1), _level(2, evidence=()), _level(3))
    assert "level_without_evidence" in chain_issues(_chain(levels=levels))


def test_level_without_evidence_in_branch_is_flagged() -> None:
    branch = _chain(levels=(_level(1, evidence=()), _level(2), _level(3)))
    assert "level_without_evidence" in chain_issues(_chain(branches=(branch,)))


@pytest.mark.parametrize(("count", "code"), [(2, "too_shallow"), (8, "too_deep")])
def test_depth_outside_3_to_7_is_flagged(count: int, code: str) -> None:
    levels = tuple(_level(i) for i in range(1, count + 1))
    assert code in chain_issues(_chain(levels=levels))


@pytest.mark.parametrize("count", [3, 7])
def test_depth_boundaries_are_valid(count: int) -> None:
    levels = tuple(_level(i) for i in range(1, count + 1))
    assert chain_issues(_chain(levels=levels)) == ()


def test_missing_root_cause_flagged_when_not_partial() -> None:
    assert "missing_root_cause" in chain_issues(_chain(root_cause=None))


def test_failed_validation_is_flagged() -> None:
    issues = chain_issues(_chain(validation=(_check(True), _check(False))))
    assert "failed_validation" in issues


def test_no_validation_flagged_when_not_partial() -> None:
    assert "no_validation" in chain_issues(_chain(validation=()))


def test_partial_chain_with_one_evidenced_level_is_valid() -> None:
    chain = WhyChain(problem=_problem(), levels=(_level(1),), root_cause=None, partial=True)
    assert chain_issues(chain) == ()


def test_partial_chain_still_requires_evidence() -> None:
    chain = WhyChain(
        problem=_problem(), levels=(_level(1, evidence=()),), root_cause=None, partial=True
    )
    assert chain_issues(chain) == ("level_without_evidence",)


def test_partial_chain_still_flags_failed_validation() -> None:
    chain = WhyChain(
        problem=_problem(),
        levels=(_level(1),),
        root_cause=None,
        validation=(_check(False),),
        partial=True,
    )
    assert chain_issues(chain) == ("failed_validation",)


def test_issues_are_a_tuple_of_str() -> None:
    issues = chain_issues(WhyChain(problem=_problem(), levels=(), root_cause=None))
    assert isinstance(issues, tuple)
    assert set(issues) >= {"too_shallow", "missing_root_cause", "no_validation"}
