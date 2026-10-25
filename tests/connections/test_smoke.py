"""Smoke test: the rootcause.connections subpackage is importable and versioned."""

import pytest

import rootcause.connections


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause.connections.__version__ == "0.1.0"
