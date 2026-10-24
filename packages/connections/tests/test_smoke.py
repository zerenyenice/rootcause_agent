"""Smoke test: the rootcause-connections distribution is importable and versioned."""

import pytest

import rootcause_connections


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_connections.__version__ == "0.1.0"
