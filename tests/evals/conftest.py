"""Shared fixtures for the manufacturing warehouse generator tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from datetime import date

    import pandas as pd

DAYS = 30
SEED = 7


@pytest.fixture(scope="session")
def tables() -> dict[str, pd.DataFrame]:
    """Generate the 30-day baseline warehouse once per test session."""
    from rootcause.evals.manufacturing import GeneratorConfig, generate

    return generate(GeneratorConfig(seed=SEED, days=DAYS))


INCIDENT_DAYS = 120
PRE_WINDOW_DAYS = 28
APPLY_SEED = 99


class Metrics:
    """Window arithmetic shared by the incident tests (pure functions over frames)."""

    @staticmethod
    def day(frame: pd.DataFrame, column: str = "ts") -> pd.Series:
        import pandas as pd

        return pd.Series(frame[column].dt.normalize())

    @staticmethod
    def span(window: tuple[date, date]) -> tuple[pd.Timestamp, pd.Timestamp]:
        """Half-open [start, end) timestamps for an inclusive date window."""
        import pandas as pd

        return pd.Timestamp(window[0]), pd.Timestamp(window[1]) + pd.Timedelta(days=1)

    @classmethod
    def in_window(cls, frame: pd.DataFrame, window: tuple[date, date]) -> pd.DataFrame:
        start, end = cls.span(window)
        return frame[(frame["ts"] >= start) & (frame["ts"] < end)]

    @classmethod
    def pre_window(cls, frame: pd.DataFrame, window: tuple[date, date]) -> pd.DataFrame:
        import pandas as pd

        start, _ = cls.span(window)
        return frame[
            (frame["ts"] >= start - pd.Timedelta(days=PRE_WINDOW_DAYS)) & (frame["ts"] < start)
        ]

    @staticmethod
    def scrap_rate(runs: pd.DataFrame) -> float:
        return float(runs["units_scrapped"].sum() / runs["units_produced"].sum())


@pytest.fixture(scope="session")
def metrics() -> type[Metrics]:
    return Metrics


@pytest.fixture(scope="session")
def config120() -> Any:
    from rootcause.evals.manufacturing import GeneratorConfig

    return GeneratorConfig(seed=SEED, days=INCIDENT_DAYS)


@pytest.fixture(scope="session")
def baseline120(config120: Any) -> dict[str, pd.DataFrame]:
    """The 120-day baseline without any incident (same seed)."""
    from rootcause.evals.manufacturing import generate

    return generate(config120)


@pytest.fixture(scope="session")
def incidents120(config120: Any) -> dict[str, Any]:
    """The six easy incidents of the 120-day config keyed by case_id."""
    from rootcause.evals.manufacturing.incidents import instantiate

    return {i.case_id: i for i in instantiate(config120, seed=SEED)}


@pytest.fixture(scope="session")
def applied120(
    baseline120: dict[str, pd.DataFrame], incidents120: dict[str, Any]
) -> dict[str, dict[str, pd.DataFrame]]:
    """Each incident applied on its own to the baseline, keyed by case_id."""
    import numpy as np

    return {
        case_id: incident.apply(baseline120, np.random.default_rng(APPLY_SEED))
        for case_id, incident in incidents120.items()
    }
