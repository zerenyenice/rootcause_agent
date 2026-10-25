"""Score one agent ``WhyChain`` against a planted case (MVP: keywords and chain validity).

Keywords are matched as whole words in the root-cause text only (the root-cause
statement and the level answers, branches included), never in evidence SQL: naming
tables in a query is not identifying the cause. Keywords the question already
contains are given away and are not scored.

The LLM judge, decoy handling and efficiency metrics come later (issue #45, MVP scope).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass

from rootcause.agents.reports import WhyChain, chain_issues
from rootcause.evals.manufacturing.incidents.manifest import CaseManifest
from rootcause.evals.question import question_for

NO_SCORABLE_KEYWORDS = "no_scorable_keywords"
MIN_KEYWORD_HITS = 1


@dataclass(frozen=True)
class CaseScore:
    """The outcome of one case: chain validity, keyword coverage and the verdict.

    ``keyword_hits`` and ``keyword_total`` count only the scorable keywords, those
    the question does not already contain.
    """

    case_id: str
    level: str
    chain_valid: bool
    issues: tuple[str, ...]
    keyword_hits: int
    keyword_total: int
    root_cause_hit: bool
    passed: bool
    error: str | None = None


def scorable_keywords(case: CaseManifest) -> tuple[str, ...]:
    """The non-blank case keywords the question does not contain (case-insensitive)."""
    question = question_for(case).casefold()
    return tuple(
        keyword
        for keyword in case.keywords
        if keyword.strip() and keyword.casefold() not in question
    )


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """A case-insensitive whole-word (or whole-phrase) pattern for ``keyword``.

    ``_`` counts as a word character, so ``day`` does not match ``today`` and
    ``downtime_events`` does not match inside ``downtime_events_v2``. The words of a
    phrase may be separated by any whitespace.
    """
    body = r"\s+".join(re.escape(word) for word in keyword.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def root_cause_texts(chain: WhyChain) -> Iterator[str]:
    """The texts keywords are matched against: root-cause statement and level answers.

    Branches are walked recursively, including their own root-cause statements.
    Evidence SQL is deliberately excluded.
    """
    if chain.root_cause is not None:
        yield chain.root_cause.statement
    for level in chain.levels:
        yield level.answer
    for branch in chain.branches:
        yield from root_cause_texts(branch)


def keyword_hits(keywords: tuple[str, ...], chain: WhyChain) -> int:
    """How many ``keywords`` occur as whole words in the chain's root-cause texts."""
    texts = tuple(root_cause_texts(chain))
    patterns = (keyword_pattern(keyword) for keyword in keywords)
    return sum(1 for pattern in patterns if any(pattern.search(text) for text in texts))


def hit_threshold(total: int) -> int:
    """Keyword hits needed for a root-cause hit: half of ``total`` rounded up, at least 1."""
    return max(MIN_KEYWORD_HITS, math.ceil(total / 2))


def failed_score(case: CaseManifest, error: str | None) -> CaseScore:
    """A failing score for a case without a scorable chain."""
    return CaseScore(
        case_id=case.case_id,
        level=case.level,
        chain_valid=False,
        issues=(),
        keyword_hits=0,
        keyword_total=len(scorable_keywords(case)),
        root_cause_hit=False,
        passed=False,
        error=error,
    )


def score_case(case: CaseManifest, chain: WhyChain | None, error: str | None = None) -> CaseScore:
    """Score ``chain`` for ``case``; a missing chain or an ``error`` always fails.

    A case with no scorable keywords has no usable ground truth and fails with
    ``error="no_scorable_keywords"`` whatever the agent answered. Otherwise the root
    cause is hit when the chain names a root cause and at least ``hit_threshold``
    scorable keywords appear in its root-cause texts; the case passes when, in
    addition, the chain breaks no ``chain_issues`` rule.
    """
    keywords = scorable_keywords(case)
    if not keywords:
        return failed_score(case, NO_SCORABLE_KEYWORDS)
    if chain is None or error is not None:
        return failed_score(case, error)
    issues = chain_issues(chain)
    hits = keyword_hits(keywords, chain)
    root_cause_hit = chain.root_cause is not None and hits >= hit_threshold(len(keywords))
    return CaseScore(
        case_id=case.case_id,
        level=case.level,
        chain_valid=issues == (),
        issues=issues,
        keyword_hits=hits,
        keyword_total=len(keywords),
        root_cause_hit=root_cause_hit,
        passed=issues == () and root_cause_hit,
        error=None,
    )
