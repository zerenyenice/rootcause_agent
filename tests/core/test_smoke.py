"""Smoke test: the rootcause package is importable and versioned."""

import pytest

import rootcause


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause.__version__ == "0.1.0"
