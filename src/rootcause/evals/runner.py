"""Evaluation runner (issue #45): ask the agent about each planted case and score it.

MVP scope: keyword and chain-validity scoring of the easy cases E1 to E3. The LLM
judge, decoys and efficiency come later.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from rootcause.agents.reports import WhyChain
from rootcause.evals.manufacturing.incidents.manifest import CaseManifest, read_cases_json
from rootcause.evals.question import question_for
from rootcause.evals.scoring import CaseScore, score_case
from rootcause.logging import get_logger

__all__ = [
    "EvalReport",
    "Investigate",
    "LevelPassRate",
    "load_cases",
    "question_for",
    "run_case",
    "run_cases",
]

# The agent sees the analyst question only; the manifest is the answer key and stays
# with the runner for scoring.
Investigate = Callable[[str], WhyChain | None]

_logger = get_logger(__name__)


def load_cases(path: Path) -> tuple[CaseManifest, ...]:
    """Read ``cases.json`` written by the generator back into frozen manifests."""
    return read_cases_json(path)


@dataclass(frozen=True)
class LevelPassRate:
    """How many cases of one difficulty level passed."""

    level: str
    passed: int
    total: int

    @property
    def rate(self) -> float:
        """Fraction of passed cases (0.0 for an empty level)."""
        return self.passed / self.total if self.total else 0.0


def _pass_rates(scores: tuple[CaseScore, ...]) -> tuple[LevelPassRate, ...]:
    """One pass rate per level, in the order levels first appear in ``scores``."""
    levels = tuple(dict.fromkeys(score.level for score in scores))
    return tuple(
        LevelPassRate(
            level=level,
            passed=sum(1 for s in scores if s.level == level and s.passed),
            total=sum(1 for s in scores if s.level == level),
        )
        for level in levels
    )


def _cell(value: object) -> str:
    """A markdown table cell: pipes escaped, newlines flattened."""
    return str(value).replace("|", "\\|").replace("\n", " ")


@dataclass(frozen=True)
class EvalReport:
    """Scores of every case plus the pass rate per level."""

    scores: tuple[CaseScore, ...]
    pass_rates: tuple[LevelPassRate, ...]

    @property
    def all_passed(self) -> bool:
        """True when there is at least one case and every case passed."""
        return bool(self.scores) and all(score.passed for score in self.scores)

    def to_json(self) -> str:
        """The report as indented JSON (scores, pass rates with rates, overall verdict)."""
        payload = {
            "all_passed": self.all_passed,
            "scores": [dataclasses.asdict(score) for score in self.scores],
            "pass_rates": [
                {**dataclasses.asdict(rate), "rate": rate.rate} for rate in self.pass_rates
            ],
        }
        return json.dumps(payload, indent=2) + "\n"

    def to_markdown(self) -> str:
        """A table with one row per case, then a table of pass rates per level."""
        lines = [
            "# Evaluation report",
            "",
            "| Case | Level | Passed | Chain valid | Scored keywords | Root cause hit "
            "| Issues | Error |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| {_cell(s.case_id)} | {_cell(s.level)} | {'yes' if s.passed else 'no'} "
            f"| {'yes' if s.chain_valid else 'no'} | {s.keyword_hits}/{s.keyword_total} "
            f"| {'yes' if s.root_cause_hit else 'no'} | {_cell(', '.join(s.issues) or '-')} "
            f"| {_cell(s.error or '-')} |"
            for s in self.scores
        ]
        lines += ["", "## Pass rates", "", "| Level | Passed | Total | Rate |"]
        lines += ["| --- | --- | --- | --- |"]
        lines += [
            f"| {_cell(r.level)} | {r.passed} | {r.total} | {r.rate:.0%} |" for r in self.pass_rates
        ]
        return "\n".join(lines) + "\n"


def run_case(case: CaseManifest, investigate: Investigate) -> CaseScore:
    """Ask ``investigate`` the question for one case and score the answer.

    The agent receives only the question; ``case`` is used for scoring alone.

    An exception from the agent becomes a failed score whose ``error`` is the
    exception type name only (messages may echo data, so they are not reported).
    """
    try:
        result = investigate(question_for(case))
    except Exception as exc:  # noqa: BLE001 - one failing case must not stop the run
        _logger.warning("eval_case_failed", case_id=case.case_id, error=type(exc).__name__)
        return score_case(case, None, error=type(exc).__name__)
    if result is not None and not isinstance(result, WhyChain):
        _logger.warning("eval_case_bad_output", case_id=case.case_id, type=type(result).__name__)
        return score_case(case, None, error=TypeError.__name__)
    return score_case(case, result)


def run_cases(cases: Iterable[CaseManifest], investigate: Investigate) -> EvalReport:
    """Run ``investigate`` over every case and collect the scores and pass rates.

    ``cases`` are the ground truth for scoring; they are never passed to the agent.
    """
    scores = tuple(run_case(case, investigate) for case in cases)
    return EvalReport(scores=scores, pass_rates=_pass_rates(scores))
