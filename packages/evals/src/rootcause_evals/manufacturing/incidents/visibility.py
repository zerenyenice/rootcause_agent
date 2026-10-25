"""Visibility check: a planted anomaly must stand out from the baseline noise.

The statistic is the unscaled, direction-signed robust z of the window median
against the ``PRE_WINDOW_DAYS`` (56) days before the window::

    z = sign * (median(window) - median(pre)) / (1.4826 * MAD(pre))

where the series is the incident's daily metric, optionally divided by the pre-window
median of the same weekday first (``weekday_adjusted``), ``MAD`` is the median absolute
deviation of the pre-window, and ``sign`` is +1 for an expected rise and -1 for a drop.
The z is not scaled by the window length: it is the shift in units of single-day
noise, so a longer window does not make a weak effect look visible. An incident is
visible when ``z >= 3``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from rootcause_evals.manufacturing.incidents.base import (
    PRE_WINDOW_DAYS,
    PlantedIncident,
    Tables,
    window_bounds,
)

Z_THRESHOLD = 3.0
MAD_TO_SIGMA = 1.4826
MIN_PRE_DAYS = 7
SCALE_FLOOR = 1e-9


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
    """Signed robust z of the window median against the preceding ``PRE_WINDOW_DAYS`` days."""
    start, end_exclusive = window_bounds(incident.window)
    index = pd.DatetimeIndex(series.index)
    pre = series[(index >= start - pd.Timedelta(days=PRE_WINDOW_DAYS)) & (index < start)].dropna()
    window = series[(index >= start) & (index < end_exclusive)].dropna()
    if len(pre) < MIN_PRE_DAYS or window.empty:
        return 0.0
    pre_n, win_n = _normalised(pre, window, incident.weekday_adjusted)
    pre_n, win_n = pre_n.dropna(), win_n.dropna()
    if pre_n.empty or win_n.empty:
        return 0.0
    center = float(pre_n.median())
    mad = float((pre_n - center).abs().median())
    scale = max(MAD_TO_SIGMA * mad, SCALE_FLOOR)
    sign = 1.0 if incident.direction == "up" else -1.0
    return float(sign * (float(win_n.median()) - center) / scale)


def assert_visible(tables: Tables, incident: PlantedIncident) -> VisibilityReport:
    """Score ``incident`` on ``tables``; the report says whether it is visible (z >= 3)."""
    z = robust_z(incident, incident.daily_metric(tables))
    return VisibilityReport(case_id=incident.case_id, z=z, visible=z >= Z_THRESHOLD)
