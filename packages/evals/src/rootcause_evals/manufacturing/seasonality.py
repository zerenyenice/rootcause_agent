"""Deterministic calendar structure shared by every baseline fact table.

Nothing in this module draws random numbers: shifts, weekends, public
holidays, the monthly planned-maintenance day of each line and the slow
throughput trend are pure functions of the configuration.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from rootcause_evals.manufacturing.config import GeneratorConfig

SHIFT_NAMES: tuple[str, str, str] = ("day", "evening", "night")
DAY_SHIFT_START_HOUR = 6
EVENING_SHIFT_START_HOUR = 14
NIGHT_SHIFT_START_HOUR = 22
WEEKEND_OUTPUT_FACTOR = 0.6
HOLIDAY_OUTPUT_FACTOR = 0.1
MAINTENANCE_DIP_FACTOR = 0.4
ANNUAL_TREND = 0.04
DAYS_PER_YEAR = 365
# Fixed public holidays (month, day) applied to every plant.
PUBLIC_HOLIDAYS: tuple[tuple[int, int], ...] = (
    (1, 1),
    (4, 23),
    (5, 1),
    (5, 19),
    (7, 15),
    (8, 30),
    (10, 29),
    (12, 25),
)
# Each line has its planned maintenance on a fixed day of the month, staggered
# so plant-wide output never drops on a single day.
MAINTENANCE_FIRST_DAY_OF_MONTH = 3
MAINTENANCE_DAY_STAGGER = 3
MAINTENANCE_MAX_DAY_OF_MONTH = 27


@dataclass(frozen=True)
class Calendar:
    """Per-period calendar arrays (length ``config.periods``) plus schedules."""

    ts: npt.NDArray[np.datetime64]
    day_index: npt.NDArray[np.int64]
    hour: npt.NDArray[np.int64]
    shift: npt.NDArray[np.str_]
    is_weekend: npt.NDArray[np.bool_]
    is_holiday: npt.NDArray[np.bool_]
    holiday_dates: npt.NDArray[np.datetime64]
    line_maintenance: pd.DataFrame  # columns: line_idx, scheduled_date
    maintenance_dip: npt.NDArray[np.bool_]  # shape (lines, periods)

    @property
    def output_factor(self) -> npt.NDArray[np.float64]:
        """Calendar multiplier on output per period (before line-specific dips)."""
        base = np.where(self.is_weekend, WEEKEND_OUTPUT_FACTOR, 1.0)
        base = np.where(self.is_holiday, HOLIDAY_OUTPUT_FACTOR, base)
        trend = 1.0 + ANNUAL_TREND * self.day_index / DAYS_PER_YEAR
        return np.asarray(base * trend, dtype=np.float64)


def shift_for_hour(hour: npt.NDArray[np.int64]) -> npt.NDArray[np.str_]:
    """Map an hour-of-day array to the three-shift pattern."""
    is_day = (hour >= DAY_SHIFT_START_HOUR) & (hour < EVENING_SHIFT_START_HOUR)
    is_evening = (hour >= EVENING_SHIFT_START_HOUR) & (hour < NIGHT_SHIFT_START_HOUR)
    return np.select([is_day, is_evening], SHIFT_NAMES[:2], default=SHIFT_NAMES[2]).astype(str)


def period_timestamps(config: GeneratorConfig) -> npt.NDArray[np.datetime64]:
    """Start timestamps of every production period in the window."""
    start = np.datetime64(config.start, "ns")
    step = np.timedelta64(config.grain_minutes, "m")
    return np.asarray(start + np.arange(config.periods) * step, dtype="datetime64[ns]")


def holiday_dates(config: GeneratorConfig) -> npt.NDArray[np.datetime64]:
    """Public holiday dates falling inside the generated window."""
    start = pd.Timestamp(config.start)
    end = start + pd.Timedelta(days=config.days)
    candidates = [
        pd.Timestamp(year=year, month=month, day=day)
        for year in range(start.year, end.year + 1)
        for month, day in PUBLIC_HOLIDAYS
    ]
    inside = sorted(stamp for stamp in candidates if start <= stamp < end)
    return np.asarray(inside, dtype="datetime64[ns]")


def line_maintenance_schedule(config: GeneratorConfig) -> pd.DataFrame:
    """One planned maintenance date per line per month inside the window."""
    start = pd.Timestamp(config.start)
    end = start + pd.Timedelta(days=config.days)
    months = pd.period_range(start, end - pd.Timedelta(days=1), freq="M")
    month_starts = months.to_timestamp(how="start")
    line_idx = np.arange(config.lines)
    day_of_month = MAINTENANCE_FIRST_DAY_OF_MONTH + MAINTENANCE_DAY_STAGGER * (
        line_idx
        % (
            (MAINTENANCE_MAX_DAY_OF_MONTH - MAINTENANCE_FIRST_DAY_OF_MONTH)
            // MAINTENANCE_DAY_STAGGER
            + 1
        )
    )
    grid_line = np.repeat(line_idx, len(month_starts))
    grid_month = np.tile(month_starts.to_numpy(), config.lines)
    offsets = np.repeat(day_of_month - 1, len(month_starts)).astype("timedelta64[D]")
    scheduled = grid_month + offsets
    frame = pd.DataFrame({"line_idx": grid_line, "scheduled_date": scheduled})
    inside = (frame["scheduled_date"] >= start) & (frame["scheduled_date"] < end)
    return frame.loc[inside].reset_index(drop=True)


def build_calendar(config: GeneratorConfig) -> Calendar:
    """Assemble the calendar arrays for ``config``."""
    ts = period_timestamps(config)
    index = pd.DatetimeIndex(ts)
    day_index = np.arange(config.periods) // config.periods_per_day
    hour = index.hour.to_numpy().astype(np.int64)
    is_weekend = index.day_of_week.to_numpy() >= 5
    holidays = holiday_dates(config)
    is_holiday = np.isin(index.normalize().to_numpy(), holidays)
    maintenance = line_maintenance_schedule(config)
    dip = np.zeros((config.lines, config.periods), dtype=bool)
    maintenance_day = (
        maintenance["scheduled_date"] - pd.Timestamp(config.start)
    ).dt.days.to_numpy()
    period_of_day = np.arange(config.periods) % config.periods_per_day
    day_shift_period = (period_of_day >= DAY_SHIFT_START_HOUR * config.periods_per_hour) & (
        period_of_day < EVENING_SHIFT_START_HOUR * config.periods_per_hour
    )
    for line, day in zip(maintenance["line_idx"].to_numpy(), maintenance_day, strict=True):
        dip[line, (day_index == day) & day_shift_period] = True
    return Calendar(
        ts=ts,
        day_index=day_index.astype(np.int64),
        hour=hour,
        shift=shift_for_hour(hour),
        is_weekend=np.asarray(is_weekend, dtype=bool),
        is_holiday=np.asarray(is_holiday, dtype=bool),
        holiday_dates=holidays,
        line_maintenance=maintenance,
        maintenance_dip=dip,
    )
