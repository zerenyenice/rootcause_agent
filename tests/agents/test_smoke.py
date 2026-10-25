"""Smoke test: the rootcause.agents subpackage is importable and versioned."""

import pytest

import rootcause.agents


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause.agents.__version__ == "0.1.0"
