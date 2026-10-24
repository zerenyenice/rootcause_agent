"""Smoke test: the rootcause-memory distribution is importable and versioned."""

import pytest

import rootcause_memory


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_memory.__version__ == "0.1.0"
