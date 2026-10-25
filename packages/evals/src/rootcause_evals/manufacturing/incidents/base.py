"""Incident base class and frame helpers shared by every archetype.

An incident is a frozen description of one planted case. ``apply`` returns a
new table mapping and never mutates its inputs; randomness comes only from the
generator passed in, so applying an incident never disturbs the baseline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar, Protocol

import numpy as np
import pandas as pd

from rootcause_evals.manufacturing.incidents.manifest import (
    AnomalyWindow,
    CaseManifest,
    ChainStep,
    Counterfactual,
    ExpectedEffect,
    MetricSpec,
    RootCause,
)

Tables = Mapping[str, pd.DataFrame]


class PlantedIncident(Protocol):
    """What ``generate`` and the visibility check need from an incident."""

    @property
    def case_id(self) -> str: ...

    @property
    def window(self) -> tuple[date, date]: ...

    @property
    def direction(self) -> str: ...

    @property
    def weekday_adjusted(self) -> bool: ...

    def apply(
        self, tables: dict[str, pd.DataFrame], rng: np.random.Generator
    ) -> dict[str, pd.DataFrame]:
        """Return new tables with the incident planted."""
        ...

    def daily_metric(self, tables: Tables) -> pd.Series[float]:
        """Daily series the anomaly is visible in."""
        ...


@dataclass(frozen=True)
class Incident(ABC):
    """One planted case: identity, inclusive date window, segment and parameters."""

    case_id: str
    split: str
    window: tuple[date, date]
    segment: Mapping[str, str]
    params: Mapping[str, Any] = field(default_factory=dict)
    benign_events: tuple[str, ...] = ()

    archetype: ClassVar[str]
    level: ClassVar[str]
    direction: ClassVar[str]
    weekday_adjusted: ClassVar[bool] = False

    @abstractmethod
    def apply(self, tables: Tables, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
        """Return new tables with the incident planted; inputs are left untouched."""

    @abstractmethod
    def daily_metric(self, tables: Tables) -> pd.Series[float]:
        """Daily series (indexed by midnight timestamps) the anomaly is visible in."""

    @abstractmethod
    def manifest_entry(self) -> CaseManifest:
        """Ground-truth record for this case."""

    def build_manifest(
        self,
        *,
        metric: MetricSpec,
        effect: ExpectedEffect,
        chain: list[ChainStep],
        root_cause: RootCause,
        counterfactual: Counterfactual,
        keywords: list[str],
    ) -> CaseManifest:
        """Assemble the manifest record from this incident's identity and the case content."""
        return CaseManifest(
            case_id=self.case_id,
            archetype=self.archetype,
            level=self.level,
            split=self.split,
            metric=metric,
            anomaly_window=AnomalyWindow(
                start=self.window[0].isoformat(), end=self.window[1].isoformat()
            ),
            segment_filters=dict(self.segment),
            expected_effect=effect,
            chain=chain,
            root_cause=root_cause,
            counterfactual=counterfactual,
            decoys=[],
            benign_events_in_window=list(self.benign_events),
            keywords=keywords,
        )


def window_bounds(window: tuple[date, date]) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Half-open ``[start, end)`` timestamps for an inclusive date window."""
    return pd.Timestamp(window[0]), pd.Timestamp(window[1]) + pd.Timedelta(days=1)


def next_ids(frame: pd.DataFrame, column: str, count: int) -> np.ndarray[Any, Any]:
    """``count`` consecutive ids following the current maximum of ``column``."""
    start = int(frame[column].max()) + 1 if len(frame) else 1
    return np.arange(start, start + count, dtype=np.int64)


def append_rows(frame: pd.DataFrame, rows: Mapping[str, Any]) -> pd.DataFrame:
    """New frame with ``rows`` (column to equal-length values) appended, dtypes preserved."""
    extra = pd.DataFrame(rows)[list(frame.columns)].astype(frame.dtypes.to_dict())
    return pd.concat([frame, extra], ignore_index=True)


def ids_of(frame: pd.DataFrame, column: str, key: str, value: str) -> list[str]:
    """Values of ``column`` for rows where ``key`` equals ``value``."""
    return [str(v) for v in frame.loc[frame[key] == value, column]]


def day_of(frame: pd.DataFrame, column: str) -> pd.Series[pd.Timestamp]:
    """Midnight timestamp of each row's ``column`` value."""
    return pd.Series(frame[column].dt.normalize(), index=frame.index)


def in_span(ts: pd.Series[pd.Timestamp], start: pd.Timestamp, end: pd.Timestamp) -> pd.Series[bool]:
    """Boolean mask of timestamps in the half-open span ``[start, end)``."""
    return (ts >= start) & (ts < end)
