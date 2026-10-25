"""Synthetic discrete-manufacturing warehouse generator (PRD section 11).

The baseline layer produces dimensions and one year of hourly fact data with
weekly seasonality, a night-shift scrap penalty, monthly planned maintenance,
public holidays, a slow throughput trend and Gaussian noise. Every random draw
comes from a single ``numpy.random.Generator`` seeded from the configuration,
so the same config always yields identical frames.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from rootcause_evals.manufacturing.baseline import build_baseline
from rootcause_evals.manufacturing.config import GeneratorConfig
from rootcause_evals.manufacturing.dimensions import build_dimensions
from rootcause_evals.manufacturing.incidents import (
    CaseManifest,
    IncidentNotVisibleError,
    assert_visible,
    instantiate,
)
from rootcause_evals.manufacturing.incidents.base import PlantedIncident

__all__ = ["GeneratorConfig", "generate", "generate_with_manifest"]

INCIDENT_STREAM = 1  # spawn key separating incident randomness from the baseline stream


def generate(
    config: GeneratorConfig,
    incidents: Sequence[PlantedIncident] | None = None,
    *,
    check_visibility: bool = True,
) -> dict[str, pd.DataFrame]:
    """Generate every warehouse table for ``config`` as pandas DataFrames.

    The baseline layer is built first; ``incidents`` are then applied in order,
    each with its own child generator so the baseline values never change. Unless
    ``check_visibility`` is false, ``IncidentNotVisibleError`` is raised when a
    planted incident does not stand out (robust z below 3) in the final tables.
    """
    rng = np.random.default_rng(config.seed)
    dimensions = build_dimensions(config, rng)
    facts = build_baseline(config, rng, dimensions)
    tables = {**dimensions, **facts}
    if not incidents:
        return tables
    children = np.random.SeedSequence([config.seed, INCIDENT_STREAM]).spawn(len(incidents))
    for incident, child in zip(incidents, children, strict=True):
        tables = incident.apply(tables, np.random.default_rng(child))
    if check_visibility:
        for incident in incidents:
            report = assert_visible(tables, incident)
            if not report.visible:
                raise IncidentNotVisibleError(report)
    return tables


def generate_with_manifest(
    config: GeneratorConfig,
) -> tuple[dict[str, pd.DataFrame], tuple[CaseManifest, ...]]:
    """Generate the warehouse with every registered incident and its ground-truth manifest."""
    incidents = instantiate(config)
    tables = generate(config, incidents)
    return tables, tuple(incident.manifest_entry() for incident in incidents)
