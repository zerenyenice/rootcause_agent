"""Synthetic discrete-manufacturing warehouse generator (PRD section 11).

The baseline layer produces dimensions and one year of hourly fact data with
weekly seasonality, a night-shift scrap penalty, monthly planned maintenance,
public holidays, a slow throughput trend and Gaussian noise. Every random draw
comes from a single ``numpy.random.Generator`` seeded from the configuration,
so the same config always yields identical frames.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rootcause_evals.manufacturing.baseline import build_baseline
from rootcause_evals.manufacturing.config import GeneratorConfig
from rootcause_evals.manufacturing.dimensions import build_dimensions

__all__ = ["GeneratorConfig", "generate"]


def generate(config: GeneratorConfig) -> dict[str, pd.DataFrame]:
    """Generate every warehouse table for ``config`` as pandas DataFrames."""
    rng = np.random.default_rng(config.seed)
    dimensions = build_dimensions(config, rng)
    facts = build_baseline(config, rng, dimensions)
    return {**dimensions, **facts}
