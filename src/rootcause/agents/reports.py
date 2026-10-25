"""WhyChain: the structured output contract of the 5 Whys root-cause subagent.

See docs/PRD.md section 4 ("Output contract", "Guardrails"). Models are frozen;
``chain_issues`` reports business-rule violations a schema alone cannot express.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MIN_DEPTH = 3
MAX_DEPTH = 7

RootCauseType = Literal["process", "design", "data", "external", "knowledge"]
CountermeasureType = Literal["immediate", "preventive", "detection"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProblemStatement(_Frozen):
    """The problem in the skill's what/when/where/impact template."""

    what: str = Field(min_length=1)
    when: str = Field(min_length=1)
    where: str = Field(min_length=1)
    impact: str = Field(min_length=1)


class Evidence(_Frozen):
    """An executed query backing a why level."""

    sql: str = Field(min_length=1)
    rows_summary: str
    result_path: str | None = None


class WhyLevel(_Frozen):
    """One why: the question, the answer and the queries that support it."""

    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    evidence: tuple[Evidence, ...]
    confidence: float = Field(ge=0.0, le=1.0)


class RootCause(_Frozen):
    """The actionable, fundamental cause the chain ends in."""

    statement: str = Field(min_length=1)
    type: RootCauseType
    confidence: float = Field(ge=0.0, le=1.0)


class ValidationCheck(_Frozen):
    """A backward (counterfactual) check of one link in the chain."""

    link: str = Field(min_length=1)
    check_sql: str = Field(min_length=1)
    passed: bool


class Countermeasure(_Frozen):
    """An immediate fix, preventive measure or detection mechanism."""

    type: CountermeasureType
    action: str = Field(min_length=1)
    owner: str | None = None


class WhyChain(_Frozen):
    """A complete or partial 5 Whys analysis; branches are nested chains."""

    problem: ProblemStatement
    levels: tuple[WhyLevel, ...]
    root_cause: RootCause | None
    validation: tuple[ValidationCheck, ...] = ()
    countermeasures: tuple[Countermeasure, ...] = ()
    branches: tuple[WhyChain, ...] = ()
    partial: bool = False
    notes: str = ""


def _own_issues(chain: WhyChain) -> tuple[str, ...]:
    """Rule violations of ``chain`` itself, ignoring its branches."""
    always = (
        ("level_without_evidence", any(not level.evidence for level in chain.levels)),
        ("failed_validation", any(not check.passed for check in chain.validation)),
    )
    complete_only = (
        ("too_shallow", len(chain.levels) < MIN_DEPTH),
        ("too_deep", len(chain.levels) > MAX_DEPTH),
        ("missing_root_cause", chain.root_cause is None),
        ("no_validation", not chain.validation),
    )
    rules = always if chain.partial else always + complete_only
    return tuple(code for code, violated in rules if violated)


def chain_issues(chain: WhyChain) -> tuple[str, ...]:
    """Return the distinct rule-violation codes of ``chain`` and its branches.

    A partial chain (budget exhausted) is exempt from the depth, root-cause and
    validation-present rules, but still needs evidence on every level and may
    not hide failed validation checks. An empty tuple means the chain is valid.
    """
    found = _own_issues(chain) + tuple(
        code for branch in chain.branches for code in chain_issues(branch)
    )
    return tuple(dict.fromkeys(found))
