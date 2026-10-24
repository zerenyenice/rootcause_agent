"""Smoke test: the rootcause-anomaly distribution is importable and versioned."""

import pytest

import rootcause_anomaly


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_anomaly.__version__ == "0.1.0"
