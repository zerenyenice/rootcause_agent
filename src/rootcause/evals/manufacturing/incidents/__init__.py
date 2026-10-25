"""Incident layer: composable planted cases on top of the deterministic baseline."""

from __future__ import annotations

from rootcause.evals.manufacturing.incidents.base import Incident
from rootcause.evals.manufacturing.incidents.manifest import (
    CaseManifest,
    read_cases_json,
    write_cases_json,
)
from rootcause.evals.manufacturing.incidents.registry import ARCHETYPES, instantiate
from rootcause.evals.manufacturing.incidents.visibility import (
    IncidentNotVisibleError,
    VisibilityReport,
    assert_visible,
)

__all__ = [
    "ARCHETYPES",
    "CaseManifest",
    "Incident",
    "IncidentNotVisibleError",
    "VisibilityReport",
    "assert_visible",
    "instantiate",
    "read_cases_json",
    "write_cases_json",
]
