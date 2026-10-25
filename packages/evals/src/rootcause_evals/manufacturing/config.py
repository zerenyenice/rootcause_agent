"""Generator configuration: a frozen, validated dataclass.

All size parameters of the synthetic warehouse live here so a run is fully
described by one immutable value (PRD section 11, generator requirement 1).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date

ALLOWED_SCALES: frozenset[int] = frozenset({1, 10})
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
# scale 1 is the hourly base grain; scale 10 is the 10-minute performance variant.
GRAIN_MINUTES_BY_SCALE: dict[int, int] = {1: 60, 10: 10}

_POSITIVE_FIELDS = (
    "days",
    "plants",
    "lines_per_plant",
    "machines_per_line",
    "products",
    "families",
    "suppliers",
    "materials",
    "operators",
)


@dataclass(frozen=True)
class GeneratorConfig:
    """Parameters of one deterministic warehouse generation."""

    seed: int = 42
    start: str = "2025-10-01"
    days: int = 365
    plants: int = 2
    lines_per_plant: int = 4
    machines_per_line: int = 5
    products: int = 30
    families: int = 5
    suppliers: int = 12
    materials: int = 20
    operators: int = 200
    scale: int = 1

    def __post_init__(self) -> None:
        for name in _POSITIVE_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        if self.scale not in ALLOWED_SCALES:
            raise ValueError(f"scale must be one of {sorted(ALLOWED_SCALES)}, got {self.scale!r}")
        if self.families > self.products:
            raise ValueError("families must not exceed products")
        try:
            date.fromisoformat(self.start)
        except ValueError as exc:
            raise ValueError(f"start must be an ISO date (YYYY-MM-DD), got {self.start!r}") from exc

    @property
    def start_date(self) -> date:
        """First day of the generated period."""
        return date.fromisoformat(self.start)

    @property
    def lines(self) -> int:
        """Total number of production lines across all plants."""
        return self.plants * self.lines_per_plant

    @property
    def machines(self) -> int:
        """Total number of machines across all lines."""
        return self.lines * self.machines_per_line

    @property
    def grain_minutes(self) -> int:
        """Length of one production period in minutes."""
        return GRAIN_MINUTES_BY_SCALE[self.scale]

    @property
    def periods_per_hour(self) -> int:
        """Number of production periods per hour."""
        return MINUTES_PER_HOUR // self.grain_minutes

    @property
    def periods_per_day(self) -> int:
        """Number of production periods per day."""
        return HOURS_PER_DAY * self.periods_per_hour

    @property
    def periods(self) -> int:
        """Total number of production periods in the generated window."""
        return self.days * self.periods_per_day

    def as_dict(self) -> dict[str, int | str]:
        """Plain mapping of the configured fields (for logging and manifests)."""
        return {field.name: getattr(self, field.name) for field in fields(self)}
