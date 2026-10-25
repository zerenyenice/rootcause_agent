"""GeneratorConfig defaults, immutability and validation."""

from __future__ import annotations

import dataclasses

import pytest

DEFAULTS = {
    "seed": 42,
    "start": "2025-10-01",
    "days": 365,
    "plants": 2,
    "lines_per_plant": 4,
    "machines_per_line": 5,
    "products": 30,
    "families": 5,
    "suppliers": 12,
    "materials": 20,
    "operators": 200,
    "scale": 1,
}


@pytest.mark.unit
@pytest.mark.parametrize(("field", "expected"), list(DEFAULTS.items()))
def test_default_value_matches_plan(field: str, expected: object) -> None:
    from rootcause.evals.manufacturing import GeneratorConfig

    config = GeneratorConfig()

    assert getattr(config, field) == expected


@pytest.mark.unit
def test_config_is_frozen_dataclass() -> None:
    from rootcause.evals.manufacturing import GeneratorConfig

    config = GeneratorConfig()

    assert dataclasses.is_dataclass(config)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.seed = 1  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize("days", [0, -1, -365])
def test_non_positive_days_rejected(days: int) -> None:
    from rootcause.evals.manufacturing import GeneratorConfig

    with pytest.raises(ValueError, match="days"):
        GeneratorConfig(days=days)


@pytest.mark.unit
@pytest.mark.parametrize("scale", [0, 2, 5, -1, 100])
def test_scale_outside_one_or_ten_rejected(scale: int) -> None:
    from rootcause.evals.manufacturing import GeneratorConfig

    with pytest.raises(ValueError, match="scale"):
        GeneratorConfig(scale=scale)


@pytest.mark.unit
def test_scale_ten_accepted() -> None:
    from rootcause.evals.manufacturing import GeneratorConfig

    assert GeneratorConfig(scale=10).scale == 10


@pytest.mark.unit
def test_overrides_are_applied() -> None:
    from rootcause.evals.manufacturing import GeneratorConfig

    config = GeneratorConfig(seed=7, days=30)

    assert (config.seed, config.days) == (7, 30)
