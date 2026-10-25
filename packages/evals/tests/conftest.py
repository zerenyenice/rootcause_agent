"""Shared fixtures for the manufacturing warehouse generator tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import pandas as pd

DAYS = 30
SEED = 7


@pytest.fixture(scope="session")
def tables() -> dict[str, pd.DataFrame]:
    """Generate the 30-day baseline warehouse once per test session."""
    from rootcause_evals.manufacturing import GeneratorConfig, generate

    return generate(GeneratorConfig(seed=SEED, days=DAYS))
