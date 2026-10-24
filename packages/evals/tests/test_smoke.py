"""Smoke test: the rootcause-evals distribution is importable and versioned."""

import pytest

import rootcause_evals


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause_evals.__version__ == "0.1.0"
