"""Smoke test: the rootcause.evals subpackage is importable and versioned."""

import pytest

import rootcause.evals


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert rootcause.evals.__version__ == "0.1.0"
