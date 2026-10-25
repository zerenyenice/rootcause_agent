"""Smoke test: the rootcause.catalog subpackage is importable and versioned."""

import pytest

import rootcause.catalog


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause.catalog.__version__ == "0.1.0"
