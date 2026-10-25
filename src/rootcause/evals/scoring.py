"""Score one agent ``WhyChain`` against a planted case (MVP: keywords and chain validity).

The LLM judge, decoy handling and efficiency metrics come later (issue #45, MVP scope).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

from rootcause.agents.reports import WhyChain, chain_issues
from rootcause.evals.manufacturing.incidents.manifest import CaseManifest


@dataclass(frozen=True)
class CaseScore:
    """The outcome of one case: chain validity, keyword coverage and the verdict."""

    case_id: str
    level: str
    chain_valid: bool
    issues: tuple[str, ...]
    keyword_hits: int
    keyword_total: int
    root_cause_hit: bool
    passed: bool
    error: str | None = None


def chain_texts(chain: WhyChain) -> Iterator[str]:
    """Every text the keywords are matched against: root cause, answers and evidence SQL.

    Branches are walked recursively, including their own root-cause statements.
    """
    if chain.root_cause is not None:
        yield chain.root_cause.statement
    for level in chain.levels:
        yield level.answer
        yield from (evidence.sql for evidence in level.evidence)
    for branch in chain.branches:
        yield from chain_texts(branch)


def keyword_hits(keywords: tuple[str, ...], chain: WhyChain) -> int:
    """How many ``keywords`` occur (case-insensitive substring) in the chain."""
    corpus = "\n".join(chain_texts(chain)).casefold()
    return sum(1 for keyword in keywords if keyword.casefold() in corpus)


def failed_score(case: CaseManifest, error: str | None) -> CaseScore:
    """A failing score for a case the agent produced no chain for."""
    return CaseScore(
        case_id=case.case_id,
        level=case.level,
        chain_valid=False,
        issues=(),
        keyword_hits=0,
        keyword_total=len(case.keywords),
        root_cause_hit=False,
        passed=False,
        error=error,
    )


def score_case(case: CaseManifest, chain: WhyChain | None, error: str | None = None) -> CaseScore:
    """Score ``chain`` for ``case``; a missing chain or an ``error`` always fails.

    The root cause is hit when the chain names a root cause and at least half the
    case keywords (rounded up) appear in it; the case passes when, in addition, the
    chain breaks no ``chain_issues`` rule.
    """
    if chain is None or error is not None:
        return failed_score(case, error)
    issues = chain_issues(chain)
    total = len(case.keywords)
    hits = keyword_hits(case.keywords, chain)
    root_cause_hit = chain.root_cause is not None and hits >= math.ceil(total / 2)
    return CaseScore(
        case_id=case.case_id,
        level=case.level,
        chain_valid=issues == (),
        issues=issues,
        keyword_hits=hits,
        keyword_total=total,
        root_cause_hit=root_cause_hit,
        passed=issues == () and root_cause_hit,
        error=None,
    )
