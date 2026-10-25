"""Fake cases and fake investigate callables for the evaluation runner tests (#45)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from typing import Any

import pytest

from rootcause.agents.reports import (
    Evidence,
    ProblemStatement,
    RootCause,
    ValidationCheck,
    WhyChain,
    WhyLevel,
)
from rootcause.evals.manufacturing.incidents.manifest import (
    AnomalyWindow,
    CaseManifest,
    ChainStep,
    Counterfactual,
    Decoy,
    ExpectedEffect,
    FrozenDict,
    MetricSpec,
)
from rootcause.evals.manufacturing.incidents.manifest import RootCause as TruthRootCause
from rootcause.evals.runner import Investigate, question_for

MIN_LEVELS = 3


def make_case(**overrides: Any) -> CaseManifest:
    """A synthetic E1-like easy case; ``overrides`` replace any top-level field."""
    case = CaseManifest(
        case_id="E1-test",
        archetype="E1",
        level="easy",
        split="dev",
        metric=MetricSpec("scrap_rate", "production_runs", "sum(s)/sum(p)", "day"),
        anomaly_window=AnomalyWindow(start="2025-03-03", end="2025-03-09"),
        segment_filters=FrozenDict({"line_id": "L03"}),
        expected_effect=ExpectedEffect(direction="up", size=0.6),
        chain=(
            ChainStep(
                cause="Scrap rose on one hot machine",
                evidence_tables=("machine_telemetry",),
                evidence_columns=("temperature_c",),
                sql_hint="SELECT avg(temperature_c) FROM machine_telemetry",
            ),
            ChainStep(
                cause="The machine overheated because its coolant pump failed",
                evidence_tables=("maintenance_events",),
                evidence_columns=("part",),
                sql_hint="SELECT part FROM maintenance_events WHERE machine_id = 'M005'",
            ),
        ),
        root_cause=TruthRootCause(
            statement="Coolant pump failure on machine M005 raised scrap on line L03",
            type="equipment",
        ),
        counterfactual=Counterfactual(
            description="Without the pump failure scrap stays flat",
            sql_hint="SELECT 1 FROM counterfactual_probe",
        ),
        decoys=(Decoy(description="A supplier lot change", why_wrong="other lines unaffected"),),
        benign_events_in_window=("shift handover drill",),
        keywords=("scrap", "coolant pump", "temperature_c", "maintenance_events", "L03", "M005"),
    )
    return dataclasses.replace(case, **overrides)


def _problem(case: CaseManifest) -> ProblemStatement:
    return ProblemStatement(
        what=f"{case.metric.name} changed",
        when=f"{case.anomaly_window.start} to {case.anomaly_window.end}",
        where=", ".join(case.segment_filters.values()) or "plant",
        impact="see metric",
    )


def _level(answer: str, sql: str | None) -> WhyLevel:
    evidence = (Evidence(sql=sql, rows_summary="rows"),) if sql else ()
    return WhyLevel(question="Why?", answer=answer, evidence=evidence, confidence=0.8)


def _cited_answer(step: ChainStep) -> str:
    """A level answer that states the cause and cites the tables and columns behind it."""
    return f"{step.cause} ({', '.join((*step.evidence_tables, *step.evidence_columns))})"


def valid_chain_for(case: CaseManifest) -> WhyChain:
    """A rule-abiding chain that restates the ground truth (it should pass).

    Each level answer states the planted cause and cites its evidence tables and
    columns, as a careful analyst would.
    """
    levels = [_level(_cited_answer(step), step.sql_hint) for step in case.chain]
    levels += [_level("Confirmed by the data", "SELECT 1")] * (MIN_LEVELS - len(levels))
    return WhyChain(
        problem=_problem(case),
        levels=tuple(levels),
        root_cause=RootCause(statement=case.root_cause.statement, type="process", confidence=0.8),
        validation=(ValidationCheck(link="root -> symptom", check_sql="SELECT 1", passed=True),),
    )


def invalid_chain_for(case: CaseManifest) -> WhyChain:
    """The right cause, but a level without executed SQL (chain rule violation)."""
    chain = valid_chain_for(case)
    first = chain.levels[0].model_copy(update={"evidence": ()})
    return chain.model_copy(update={"levels": (first, *chain.levels[1:])})


def wrong_cause_chain_for(case: CaseManifest) -> WhyChain:
    """A valid chain that blames something unrelated to every keyword."""
    return WhyChain(
        problem=_problem(case),
        levels=tuple(_level(f"Operators were tired {n}", "SELECT 2") for n in range(MIN_LEVELS)),
        root_cause=RootCause(statement="Staff fatigue", type="knowledge", confidence=0.4),
        validation=(ValidationCheck(link="fatigue", check_sql="SELECT 2", passed=True),),
    )


GAMING_SQL = (
    "SELECT * FROM maintenance_events, work_orders, downtime_events, business_events WHERE day = 1"
)


def gaming_chain_for(question: str, case: CaseManifest) -> WhyChain:
    """The review's game: blame staff fatigue, echo the question in every answer and
    name every event table in every level's evidence SQL."""
    chain = wrong_cause_chain_for(case)
    levels = tuple(_level(f"Looked at {question}", GAMING_SQL) for _ in range(MIN_LEVELS))
    return chain.model_copy(update={"levels": levels})


def oracle(cases: Iterable[CaseManifest], build: Callable[[CaseManifest], WhyChain]) -> Investigate:
    """An agent that answers from a question-to-case lookup it closes over.

    The runner passes the question only; the test, not the runner, holds the answer key.
    """
    lookup = {question_for(case): case for case in cases}

    def investigate(question: str) -> WhyChain:
        return build(lookup[question])

    return investigate


class FakeAgents:
    """Factories of question-only investigate callables over the given ``cases``."""

    @staticmethod
    def valid(cases: Iterable[CaseManifest]) -> Investigate:
        return oracle(cases, valid_chain_for)

    @staticmethod
    def invalid(cases: Iterable[CaseManifest]) -> Investigate:
        return oracle(cases, invalid_chain_for)

    @staticmethod
    def wrong_cause(cases: Iterable[CaseManifest]) -> Investigate:
        return oracle(cases, wrong_cause_chain_for)

    @staticmethod
    def raising(cases: Iterable[CaseManifest]) -> Investigate:
        def investigate(question: str) -> WhyChain:
            raise RuntimeError(f"agent crashed while reading {question}")

        return investigate

    @staticmethod
    def none(cases: Iterable[CaseManifest]) -> Investigate:
        def investigate(question: str) -> None:
            return None

        return investigate


@pytest.fixture
def case() -> CaseManifest:
    return make_case()


@pytest.fixture
def agents() -> type[FakeAgents]:
    return FakeAgents


@pytest.fixture
def case_factory() -> Callable[..., CaseManifest]:
    """``make_case``: a synthetic case with top-level field overrides."""
    return make_case


@pytest.fixture
def valid_chain() -> Callable[[CaseManifest], WhyChain]:
    """``valid_chain_for``: a passing chain built from a case's ground truth."""
    return valid_chain_for


class Chains:
    """The chain builders, for tests that score chains without running an agent."""

    valid = staticmethod(valid_chain_for)
    invalid = staticmethod(invalid_chain_for)
    wrong_cause = staticmethod(wrong_cause_chain_for)
    gaming = staticmethod(gaming_chain_for)


@pytest.fixture
def chains() -> type[Chains]:
    return Chains
