"""Smoke test: the rootcause-catalog distribution is importable and versioned."""

import pytest

import rootcause_catalog


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_catalog.__version__ == "0.1.0"
