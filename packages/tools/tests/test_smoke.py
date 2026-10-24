"""Smoke test: the rootcause-tools distribution is importable and versioned."""

import pytest

import rootcause_tools


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_tools.__version__ == "0.1.0"
