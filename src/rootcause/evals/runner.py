"""Evaluation runner (issue #45): ask the agent about each planted case and score it.

MVP scope: keyword and chain-validity scoring of the easy cases E1 to E3. The LLM
judge, decoys and efficiency come later.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rootcause.agents.reports import WhyChain
from rootcause.evals.manufacturing.incidents.manifest import CaseManifest, read_cases_json
from rootcause.evals.scoring import CaseScore, score_case
from rootcause.logging import get_logger

Investigate = Callable[[str, CaseManifest], WhyChain | None]

ID_SUFFIX = "_id"
PERCENT = 100

_logger = get_logger(__name__)


def load_cases(path: Path) -> tuple[CaseManifest, ...]:
    """Read ``cases.json`` written by the generator back into frozen manifests."""
    return read_cases_json(path)


def _segment_phrase(filters: Mapping[str, str]) -> str:
    """``{"line_id": "L03"}`` becomes ``" for line L03"``; empty filters give ``""``."""
    parts = [f"{key.removesuffix(ID_SUFFIX)} {filters[key]}" for key in sorted(filters)]
    return f" for {' and '.join(parts)}" if parts else ""


def question_for(case: CaseManifest) -> str:
    """The analyst question for ``case``: metric, direction, size, window and segment.

    Built only from the observable symptom, so it never leaks the root cause, the
    chain causes, the keywords, the decoys or any SQL hint.
    """
    effect = case.expected_effect
    window = case.anomaly_window
    when = (
        f"on {window.start}"
        if window.start == window.end
        else f"between {window.start} and {window.end}"
    )
    size = round(effect.size * PERCENT)
    return (
        f"Why did {case.metric.name} go {effect.direction} by about {size}% "
        f"{when}{_segment_phrase(case.segment_filters)}?"
    )


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
            "| Case | Level | Passed | Chain valid | Keywords | Root cause hit | Issues | Error |",
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
    """Ask ``investigate`` about one case and score the answer.

    An exception from the agent becomes a failed score whose ``error`` is the
    exception type name only (messages may echo data, so they are not reported).
    """
    try:
        result = investigate(question_for(case), case)
    except Exception as exc:  # noqa: BLE001 - one failing case must not stop the run
        _logger.warning("eval_case_failed", case_id=case.case_id, error=type(exc).__name__)
        return score_case(case, None, error=type(exc).__name__)
    if result is not None and not isinstance(result, WhyChain):
        _logger.warning("eval_case_bad_output", case_id=case.case_id, type=type(result).__name__)
        return score_case(case, None, error=TypeError.__name__)
    return score_case(case, result)


def run_cases(cases: Iterable[CaseManifest], investigate: Investigate) -> EvalReport:
    """Run ``investigate`` over every case and collect the scores and pass rates."""
    scores = tuple(run_case(case, investigate) for case in cases)
    return EvalReport(scores=scores, pass_rates=_pass_rates(scores))
