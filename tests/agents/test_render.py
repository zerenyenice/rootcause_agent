"""Markdown rendering of a WhyChain (issue #35)."""

import re
from typing import Any

from rootcause.agents.render import render_why_chain  # type: ignore[import-not-found]
from rootcause.agents.reports import (  # type: ignore[import-not-found]
    Countermeasure,
    Evidence,
    ProblemStatement,
    RootCause,
    ValidationCheck,
    WhyChain,
    WhyLevel,
)


def _level(n: int, answer: str = "", sql: str = "") -> WhyLevel:
    return WhyLevel(
        question=f"Why question {n}?",
        answer=answer or f"answer number {n}",
        evidence=(Evidence(sql=sql or f"SELECT {n} AS marker_{n}", rows_summary=f"rows {n}"),),
        confidence=0.85,
    )


def _chain(**overrides: Any) -> WhyChain:
    base: dict[str, Any] = {
        "problem": ProblemStatement(
            what="Scrap spike", when="Week 12", where="Line 3", impact="4% yield loss"
        ),
        "levels": (_level(1), _level(2), _level(3)),
        "root_cause": RootCause(statement="Missing SOP", type="process", confidence=0.7),
        "validation": (ValidationCheck(link="Why 3 -> Why 2", check_sql="SELECT 9", passed=True),),
        "countermeasures": (
            Countermeasure(type="preventive", action="Write the SOP", owner="QA lead"),
        ),
    }
    return WhyChain(**{**base, **overrides})


def test_renders_all_sections() -> None:
    out = render_why_chain(_chain())
    for heading in (
        "Problem Statement",
        "Why Chain",
        "Root Cause",
        "Validation",
        "Countermeasures",
    ):
        assert heading in out


def test_problem_statement_fields_are_shown() -> None:
    out = render_why_chain(_chain())
    for text in ("Scrap spike", "Week 12", "Line 3", "4% yield loss"):
        assert text in out


def test_levels_are_numbered_with_answer_and_confidence() -> None:
    out = render_why_chain(_chain())
    for n in (1, 2, 3):
        assert f"Why {n}" in out
        assert f"answer number {n}" in out
    assert out.index("Why 1") < out.index("Why 2") < out.index("Why 3")
    assert "85%" in out


def test_each_evidence_sql_is_in_a_sql_fence() -> None:
    out = render_why_chain(_chain())
    blocks = re.findall(r"```sql\n(.*?)\n```", out, flags=re.DOTALL)
    for n in (1, 2, 3):
        assert any(f"SELECT {n} AS marker_{n}" in b for b in blocks)


def test_root_cause_and_countermeasure_are_shown() -> None:
    out = render_why_chain(_chain())
    assert "Missing SOP" in out
    assert "Write the SOP" in out
    assert "QA lead" in out


def test_no_root_cause_renders_not_determined() -> None:
    out = render_why_chain(_chain(root_cause=None, partial=True))
    assert "Not determined" in out


def test_determined_root_cause_does_not_say_not_determined() -> None:
    assert "Not determined" not in render_why_chain(_chain())


def test_partial_chain_shows_notice_and_complete_chain_does_not() -> None:
    partial = render_why_chain(_chain(root_cause=None, partial=True, notes="budget exhausted"))
    assert "Partial result" in partial
    assert "budget exhausted" in partial
    assert "Partial result" not in render_why_chain(_chain())


def test_branches_render_under_branch_heading() -> None:
    branch = _chain(levels=(_level(1, answer="branch-only answer"),) * 3, branches=())
    out = render_why_chain(_chain(branches=(branch,)))
    assert "Branch" in out
    assert "branch-only answer" in out
    assert out.index("Branch") < out.index("branch-only answer")


def test_no_branch_heading_without_branches() -> None:
    assert "Branch" not in render_why_chain(_chain())


def test_backticks_in_answer_do_not_break_fences() -> None:
    nasty = "contains ``` fence and ```sql too"
    out = render_why_chain(_chain(levels=(_level(1, answer=nasty), _level(2), _level(3))))
    fences = [ln for ln in out.splitlines() if ln.lstrip().startswith("```")]
    # Only renderer-owned fences may start a line: the answer must not open/close one.
    assert len(fences) % 2 == 0
    assert len(fences) == 6  # three evidence blocks, open + close each


def test_backticks_in_sql_do_not_break_fences() -> None:
    out = render_why_chain(
        _chain(levels=(_level(1, sql="SELECT '```' AS x"), _level(2), _level(3)))
    )
    fences = [ln for ln in out.splitlines() if ln.lstrip().startswith("```")]
    assert len(fences) % 2 == 0


def test_unicode_and_markdown_specials_are_preserved() -> None:
    out = render_why_chain(_chain(levels=(_level(1, answer="café \U0001f600 *x* _y_"),) * 3))
    assert "café \U0001f600" in out


def test_empty_levels_render_without_error() -> None:
    out = render_why_chain(_chain(levels=(), root_cause=None, partial=True))
    assert "Problem Statement" in out
