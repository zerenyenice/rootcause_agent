"""Visibility check: a planted anomaly must stand out from the baseline noise.

For a window of ``L`` days, the statistic is the direction-signed robust z of the window
mean against every rolling ``L``-day mean inside the ``PRE_WINDOW_DAYS`` (56) days before it::

    z = sign * (mean(window) - median(R)) / (1.4826 * max(MAD(R), 0.01 * |median(R)|))

where ``R`` holds the rolling ``L``-day means of the pre-window, the series is optionally
divided by the pre-window median of the same weekday first (``weekday_adjusted``), and
``sign`` is +1 for an expected rise and -1 for a drop. Noise is thus measured at the same
aggregation level as the window, so neither a long window nor a single noisy day distorts
the score. An incident is visible when ``z >= 3``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from rootcause.evals.manufacturing.incidents.base import (
    PRE_WINDOW_DAYS,
    PlantedIncident,
    Tables,
    window_bounds,
)

Z_THRESHOLD = 3.0
MAD_TO_SIGMA = 1.4826
MIN_PRE_DAYS = 7
MAD_FLOOR_SHARE = 0.01


@dataclass(frozen=True)
class VisibilityReport:
    """Robust z-score of one incident and whether it clears the threshold."""

    case_id: str
    z: float
    visible: bool


class IncidentNotVisibleError(Exception):
    """Raised when a planted incident does not stand out from the baseline."""

    def __init__(self, report: VisibilityReport) -> None:
        super().__init__(
            f"incident {report.case_id} is not visible: robust z {report.z:.2f} < {Z_THRESHOLD}"
        )
        self.report = report


def _normalised(
    pre: pd.Series[float], window: pd.Series[float], weekday_adjusted: bool
) -> tuple[pd.Series[float], pd.Series[float]]:
    if not weekday_adjusted:
        return pre, window
    pre_days, win_days = pd.DatetimeIndex(pre.index), pd.DatetimeIndex(window.index)
    reference = pre.groupby(pre_days.day_of_week).median()
    pre_ref = pre_days.day_of_week.map(reference.to_dict()).to_numpy(dtype=float)
    win_ref = win_days.day_of_week.map(reference.to_dict()).to_numpy(dtype=float)
    return pre / np.where(pre_ref == 0, np.nan, pre_ref), window / np.where(
        win_ref == 0, np.nan, win_ref
    )


def robust_z(incident: PlantedIncident, series: pd.Series[float]) -> float:
    """Signed robust z of the window mean against rolling window-length means of the pre-window."""
    start, end_exclusive = window_bounds(incident.window)
    index = pd.DatetimeIndex(series.index)
    pre = series[(index >= start - pd.Timedelta(days=PRE_WINDOW_DAYS)) & (index < start)].dropna()
    window = series[(index >= start) & (index < end_exclusive)].dropna()
    length = (incident.window[1] - incident.window[0]).days + 1
    if len(pre) < max(MIN_PRE_DAYS, length) or window.empty:
        return 0.0
    pre_n, win_n = _normalised(pre, window, incident.weekday_adjusted)
    pre_n, win_n = pre_n.dropna(), win_n.dropna()
    if len(pre_n) < length or win_n.empty:
        return 0.0
    rolling = pre_n.rolling(length).mean().dropna().to_numpy(dtype=float)
    center = float(np.median(rolling))
    mad = max(float(np.median(np.abs(rolling - center))), MAD_FLOOR_SHARE * abs(center))
    if mad == 0.0:
        return 0.0
    sign = 1.0 if incident.direction == "up" else -1.0
    return float(sign * (float(win_n.mean()) - center) / (MAD_TO_SIGMA * mad))


def assert_visible(tables: Tables, incident: PlantedIncident) -> VisibilityReport:
    """Score ``incident`` on ``tables``; the report says whether it is visible (z >= 3)."""
    z = robust_z(incident, incident.daily_metric(tables))
    return VisibilityReport(case_id=incident.case_id, z=z, visible=z >= Z_THRESHOLD)
