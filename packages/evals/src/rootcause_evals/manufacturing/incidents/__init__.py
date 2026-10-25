"""Incident layer: composable planted cases on top of the deterministic baseline."""

from __future__ import annotations

from rootcause_evals.manufacturing.incidents.base import Incident
from rootcause_evals.manufacturing.incidents.manifest import CaseManifest, write_cases_json
from rootcause_evals.manufacturing.incidents.registry import ARCHETYPES, instantiate
from rootcause_evals.manufacturing.incidents.visibility import (
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
    "write_cases_json",
]
