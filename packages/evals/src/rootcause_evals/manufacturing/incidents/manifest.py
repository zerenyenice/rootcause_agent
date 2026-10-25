"""Ground-truth manifest (PRD section 11): one frozen record per planted case."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn


class FrozenDict(dict[str, str]):
    """Read-only string mapping: every mutator raises ``TypeError``.

    Unlike ``MappingProxyType`` it survives ``dataclasses.asdict`` (deep copy) and
    serialises with ``json`` as a plain object.
    """

    def _readonly(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("FrozenDict is read-only")

    __setitem__ = _readonly
    __delitem__ = _readonly
    __ior__ = _readonly
    update = _readonly
    pop = _readonly
    popitem = _readonly
    clear = _readonly
    setdefault = _readonly


@dataclass(frozen=True)
class MetricSpec:
    """The affected metric: name, source table, SQL expression and grain."""

    name: str
    table: str
    expression: str
    grain: str


@dataclass(frozen=True)
class AnomalyWindow:
    """Inclusive anomaly window as ISO dates."""

    start: str
    end: str


@dataclass(frozen=True)
class ExpectedEffect:
    """Direction and relative size (0.3 means 30 percent) of the planted effect."""

    direction: str
    size: float
    unit: str = "relative_change"


@dataclass(frozen=True)
class ChainStep:
    """One cause statement with the tables and columns that evidence it."""

    cause: str
    evidence_tables: tuple[str, ...]
    evidence_columns: tuple[str, ...]
    sql_hint: str


@dataclass(frozen=True)
class RootCause:
    """Root cause statement and type (process, design, data, external, equipment, knowledge)."""

    statement: str
    type: str


@dataclass(frozen=True)
class Counterfactual:
    """What would have removed the anomaly, with a query to check it."""

    description: str
    sql_hint: str


@dataclass(frozen=True)
class Decoy:
    """A coinciding event that is not the cause, and why."""

    description: str
    why_wrong: str


@dataclass(frozen=True)
class CaseManifest:
    """Every PRD section 11 ground-truth field of one planted case."""

    case_id: str
    archetype: str
    level: str
    split: str
    metric: MetricSpec
    anomaly_window: AnomalyWindow
    segment_filters: Mapping[str, str]
    expected_effect: ExpectedEffect
    chain: tuple[ChainStep, ...]
    root_cause: RootCause
    counterfactual: Counterfactual
    decoys: tuple[Decoy, ...]
    benign_events_in_window: tuple[str, ...]
    keywords: tuple[str, ...]


def write_cases_json(cases: Iterable[CaseManifest], path: Path) -> None:
    """Write ``cases`` as a JSON list with sorted keys (byte-stable; ``[]`` when empty)."""
    payload = [dataclasses.asdict(case) for case in cases]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
