"""Markdown rendering of a WhyChain, following the 5 Whys skill's Output Format.

Free text comes from the model and the database, so it is never allowed to open
or close a code block: a line that starts with a fence marker is escaped, and
every SQL fence is longer than any backtick run inside the SQL it wraps.
"""

import re

from rootcause.agents.reports import (
    Countermeasure,
    Evidence,
    ProblemStatement,
    RootCause,
    ValidationCheck,
    WhyChain,
    WhyLevel,
)

MIN_FENCE = 3
MAX_HEADING = 6
_BACKTICK_RUN = re.compile(r"`+")
_FENCE_START = re.compile(r"^(\s*)([`~]{3})")


def _neutralise(text: str) -> str:
    """Escape fence markers at line starts so free text cannot open a code block."""
    return "\n".join(_FENCE_START.sub(r"\1\\\2", line) for line in text.splitlines())


def _inline(text: str) -> str:
    """Collapse to one line (headings, list items, table cells)."""
    return " ".join(text.split())


def _cell(text: str) -> str:
    return _inline(text).replace("|", "\\|")


def _longest_run(text: str) -> int:
    return max((len(run) for run in _BACKTICK_RUN.findall(text)), default=0)


def _code(text: str) -> str:
    """Inline code span whose delimiter is longer than any backtick run inside."""
    flat = _inline(text)
    ticks = "`" * (_longest_run(flat) + 1)
    return f"{ticks} {flat} {ticks}"


def _sql_fence(sql: str) -> str:
    fence = "`" * max(MIN_FENCE, _longest_run(sql) + 1)
    return f"{fence}sql\n{sql}\n{fence}"


def _heading(level: int, text: str) -> str:
    return f"{'#' * min(level, MAX_HEADING)} {_inline(text)}"


def _percent(confidence: float) -> str:
    return f"{confidence:.0%}"


def _problem(problem: ProblemStatement, h: int) -> str:
    rows = (
        ("What", problem.what),
        ("When", problem.when),
        ("Where", problem.where),
        ("Impact", problem.impact),
    )
    body = "\n".join(f"- **{label}:** {_inline(value)}" for label, value in rows)
    return f"{_heading(h, 'Problem Statement')}\n\n{body}"


def _evidence(index: int, evidence: Evidence) -> str:
    source = f" (rows: {_code(evidence.result_path)})" if evidence.result_path else ""
    summary = _neutralise(evidence.rows_summary)
    return f"Evidence {index}{source}: {summary}\n\n{_sql_fence(evidence.sql)}"


def _level(n: int, level: WhyLevel, h: int) -> str:
    evidence = "\n\n".join(_evidence(i, ev) for i, ev in enumerate(level.evidence, 1))
    parts = (
        _heading(h, f"Why {n}: {level.question}"),
        f"**Answer:** {_neutralise(level.answer)}",
        f"**Confidence:** {_percent(level.confidence)}",
        evidence or "_No evidence recorded._",
    )
    return "\n\n".join(parts)


def _levels(levels: tuple[WhyLevel, ...], h: int) -> str:
    body = "\n\n".join(_level(n, level, h + 1) for n, level in enumerate(levels, 1))
    return f"{_heading(h, 'Why Chain')}\n\n{body or '_No levels recorded._'}"


def _root_cause(root: RootCause | None, h: int) -> str:
    if root is None:
        body = "- **Identified cause:** Not determined"
    else:
        body = (
            f"- **Identified cause:** {_inline(root.statement)}\n"
            f"- **Type:** {root.type}\n"
            f"- **Confidence:** {_percent(root.confidence)}"
        )
    return f"{_heading(h, 'Root Cause')}\n\n{body}"


def _check(check: ValidationCheck) -> str:
    mark = "passed" if check.passed else "FAILED"
    return f"- [{mark}] {_inline(check.link)}: {_code(check.check_sql)}"


def _validation(checks: tuple[ValidationCheck, ...], h: int) -> str:
    body = "\n".join(_check(c) for c in checks) or "_No validation checks recorded._"
    return f"{_heading(h, 'Validation')}\n\n{body}"


def _countermeasure(cm: Countermeasure) -> str:
    return f"| {cm.type.capitalize()} | {_cell(cm.action)} | {_cell(cm.owner or '-')} |"


def _countermeasures(items: tuple[Countermeasure, ...], h: int) -> str:
    if not items:
        return f"{_heading(h, 'Countermeasures')}\n\n_None recorded._"
    rows = "\n".join(_countermeasure(cm) for cm in items)
    table = f"| Type | Action | Owner |\n|------|--------|-------|\n{rows}"
    return f"{_heading(h, 'Countermeasures')}\n\n{table}"


def _notice(chain: WhyChain) -> tuple[str, ...]:
    notes = _neutralise(chain.notes)
    if chain.partial:
        reason = notes or "the analysis stopped before the chain was complete."
        return (f"> **Partial result:** {reason}",)
    return (f"**Notes:** {notes}",) if notes else ()


def _sections(chain: WhyChain, h: int) -> tuple[str, ...]:
    branches = tuple(
        f"{_heading(h, f'Branch {i}')}\n\n" + "\n\n".join(_sections(branch, h + 1))
        for i, branch in enumerate(chain.branches, 1)
    )
    return (
        *_notice(chain),
        _problem(chain.problem, h),
        _levels(chain.levels, h),
        _root_cause(chain.root_cause, h),
        _validation(chain.validation, h),
        _countermeasures(chain.countermeasures, h),
        *branches,
    )


def render_why_chain(chain: WhyChain) -> str:
    """Render ``chain`` (and its branches) as a markdown report."""
    title = _heading(2, f"5 Whys Analysis: {chain.problem.what}")
    return "\n\n".join((title, *_sections(chain, 3))) + "\n"
