"""Smoke test: the rootcause-agents distribution is importable and versioned."""

import pytest

import rootcause_agents


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_agents.__version__ == "0.1.0"
