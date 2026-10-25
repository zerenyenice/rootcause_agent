"""The analyst question asked about a planted case (issue #45).

Kept apart from the runner so scoring can discount keywords the question gives away.
"""

from __future__ import annotations

from collections.abc import Mapping

from rootcause.evals.manufacturing.incidents.manifest import CaseManifest

ID_SUFFIX = "_id"
PERCENT = 100


def _segment_phrase(filters: Mapping[str, str]) -> str:
    """``{"line_id": "L03"}`` becomes ``" for line L03"``; empty filters give ``""``."""
    parts = [f"{key.removesuffix(ID_SUFFIX)} {filters[key]}" for key in sorted(filters)]
    return f" for {' and '.join(parts)}" if parts else ""


def question_for(case: CaseManifest) -> str:
    """The analyst question for ``case``: metric, direction, size, window and segment.

    Built only from the observable symptom, so it never leaks the root cause, the
    chain causes, the decoys or any SQL hint. Keywords it does contain (the metric
    name, the segment) are not scored.
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
