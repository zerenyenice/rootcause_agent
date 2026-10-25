"""Smoke test: the rootcause.api subpackage is importable and versioned."""

import pytest

import rootcause.api


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause.api.__version__ == "0.1.0"
