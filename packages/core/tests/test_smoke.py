"""Smoke test: the rootcause-core distribution is importable and versioned."""

import pytest

import rootcause_core


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_core.__version__ == "0.1.0"
