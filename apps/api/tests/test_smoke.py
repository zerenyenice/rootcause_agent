"""Smoke test: the rootcause-api distribution is importable and versioned."""

import pytest

import rootcause_api


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_api.__version__ == "0.1.0"
