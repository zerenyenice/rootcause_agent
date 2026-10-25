"""Dimension tables: plants, lines, machines, products, suppliers, lots, people.

Every builder receives the shared ``numpy.random.Generator`` and returns a new
DataFrame; nothing is mutated in place and no per-row Python loops run over
large tables.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd

from rootcause.evals.manufacturing.config import GeneratorConfig
from rootcause.evals.manufacturing.seasonality import SHIFT_NAMES

MACHINE_TYPES: tuple[str, ...] = ("press", "welder", "cnc_mill", "paint_robot", "assembly_cell")
FAMILY_NAMES: tuple[str, ...] = ("motors", "pumps", "control_boards", "housings", "heating_units")
CITIES: tuple[tuple[str, str], ...] = (
    ("Izmir", "Europe/Istanbul"),
    ("Gdansk", "Europe/Warsaw"),
    ("Porto", "Europe/Lisbon"),
    ("Brno", "Europe/Prague"),
    ("Leon", "America/Mexico_City"),
)
SUPPLIER_COUNTRIES: tuple[str, ...] = ("TR", "DE", "PL", "CN", "IT", "CZ")
MATERIAL_NAMES: tuple[str, ...] = (
    "steel_sheet",
    "copper_wire",
    "abs_resin",
    "aluminium_billet",
    "silicone_seal",
    "pcb_laminate",
    "ceramic_insulator",
    "stainless_tube",
    "magnet_wire",
    "powder_coat",
)
LOTS_PER_YEAR = 2400
LOT_LEAD_DAYS = 30
ALTERNATE_SUPPLIER_SHARE = 0.15
CERTIFIED_SHARE = 0.95
INSPECTOR_SHARE = 0.10
TECHNICIAN_SHARE = 0.05
UNCERTIFIED_SHARE = 0.08
LINE_SWAP_SHARE = 0.10
DAYS_PER_YEAR = 365


def make_ids(prefix: str, count: int, width: int) -> npt.NDArray[np.str_]:
    """Zero-padded identifiers such as ``L01`` or ``M007``."""
    return np.array([f"{prefix}{i:0{width}d}" for i in range(1, count + 1)], dtype=str)


def _dates(start: str, offsets_days: npt.NDArray[np.int64]) -> npt.NDArray[np.datetime64]:
    base = np.datetime64(start, "D")
    return np.asarray(base + offsets_days.astype("timedelta64[D]"), dtype="datetime64[ns]")


def build_plants(config: GeneratorConfig) -> pd.DataFrame:
    """Plants with city and timezone."""
    cities = [CITIES[i % len(CITIES)] for i in range(config.plants)]
    return pd.DataFrame(
        {
            "plant_id": make_ids("P", config.plants, 2),
            "plant_name": [f"Plant {city}" for city, _ in cities],
            "city": [city for city, _ in cities],
            "timezone": [tz for _, tz in cities],
        }
    )


def build_lines(config: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    """Production lines per plant with commissioning dates."""
    plant_idx = np.arange(config.lines) // config.lines_per_plant
    years_ago = rng.integers(2, 15, size=config.lines)
    return pd.DataFrame(
        {
            "line_id": make_ids("L", config.lines, 2),
            "plant_id": make_ids("P", config.plants, 2)[plant_idx],
            "line_name": [f"Line {i + 1}" for i in range(config.lines)],
            "commissioned_date": _dates(config.start, -DAYS_PER_YEAR * years_ago),
        }
    )


def build_machines(
    config: GeneratorConfig, rng: np.random.Generator, lines: pd.DataFrame
) -> pd.DataFrame:
    """Machines per line, installed strictly before the window start."""
    line_idx = np.arange(config.machines) // config.machines_per_line
    position = np.arange(config.machines) % config.machines_per_line
    commissioned = lines["commissioned_date"].to_numpy()[line_idx]
    start = pd.Timestamp(config.start)
    line_age_days = (start - pd.DatetimeIndex(commissioned)).days.to_numpy(dtype=np.int64)
    # Strictly below the line's age, so install_date is always before the window start.
    install_lag = (rng.random(config.machines) * line_age_days).astype(np.int64)
    return pd.DataFrame(
        {
            "machine_id": make_ids("M", config.machines, 3),
            "line_id": lines["line_id"].to_numpy()[line_idx],
            "machine_type": np.array(MACHINE_TYPES, dtype=str)[position % len(MACHINE_TYPES)],
            "install_date": commissioned + install_lag.astype("timedelta64[D]"),
            "firmware_version": [
                f"{v}.{r}" for v, r in rng.integers(1, 6, size=(config.machines, 2))
            ],
        }
    )


def build_product_families(config: GeneratorConfig) -> pd.DataFrame:
    """Product families."""
    names = [FAMILY_NAMES[i % len(FAMILY_NAMES)] for i in range(config.families)]
    return pd.DataFrame({"family_id": make_ids("F", config.families, 2), "family_name": names})


def build_products(config: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    """Products with family, cycle time, complexity, spec and material."""
    complexity = np.round(rng.uniform(1.0, 5.0, size=config.products), 1)
    cycle = np.round(25.0 + 12.0 * complexity + rng.normal(0.0, 3.0, size=config.products), 1)
    target = np.round(rng.uniform(10.0, 120.0, size=config.products), 2)
    tolerance = np.round(target * rng.uniform(0.005, 0.02, size=config.products), 3)
    return pd.DataFrame(
        {
            "product_id": make_ids("PR", config.products, 3),
            "family_id": make_ids("F", config.families, 2)[
                np.arange(config.products) % config.families
            ],
            "product_name": [f"Component {i + 1:03d}" for i in range(config.products)],
            "complexity_score": complexity,
            "standard_cycle_time_s": np.maximum(cycle, 10.0),
            "spec_target_mm": target,
            "spec_tolerance_mm": tolerance,
            "material_id": make_ids("MT", config.materials, 2)[
                np.arange(config.products) % config.materials
            ],
        }
    )


def build_suppliers(config: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    """Suppliers with country and onboarding date."""
    countries = np.array(SUPPLIER_COUNTRIES, dtype=str)
    return pd.DataFrame(
        {
            "supplier_id": make_ids("S", config.suppliers, 2),
            "supplier_name": [f"Supplier {i + 1}" for i in range(config.suppliers)],
            "country": countries[rng.integers(0, len(countries), size=config.suppliers)],
            "onboarded_date": _dates(
                config.start,
                -rng.integers(DAYS_PER_YEAR, 12 * DAYS_PER_YEAR, size=config.suppliers),
            ),
        }
    )


def build_materials(config: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    """Materials with spec target, tolerance and primary supplier."""
    idx = np.arange(config.materials)
    target = np.round(rng.uniform(1.0, 50.0, size=config.materials), 3)
    return pd.DataFrame(
        {
            "material_id": make_ids("MT", config.materials, 2),
            "material_name": [
                f"{MATERIAL_NAMES[i % len(MATERIAL_NAMES)]}_{i // len(MATERIAL_NAMES) + 1}"
                for i in idx
            ],
            "unit": "kg",
            "spec_target": target,
            "spec_tolerance": np.round(target * 0.03, 4),
            "primary_supplier_id": make_ids("S", config.suppliers, 2)[idx % config.suppliers],
        }
    )


def build_material_lots(
    config: GeneratorConfig, rng: np.random.Generator, materials: pd.DataFrame
) -> pd.DataFrame:
    """Opening stock (one lot per material) plus lots received through the window."""
    n_random = round(config.days * LOTS_PER_YEAR / DAYS_PER_YEAR)
    material_idx = np.concatenate(
        [np.arange(config.materials), rng.integers(0, config.materials, size=n_random)]
    )
    n = len(material_idx)
    received = np.concatenate(
        [
            np.full(config.materials, -1, dtype=np.int64),
            rng.integers(-LOT_LEAD_DAYS, config.days, size=n_random),
        ]
    )
    primary = materials["primary_supplier_id"].to_numpy()[material_idx]
    alternate = make_ids("S", config.suppliers, 2)[rng.integers(0, config.suppliers, size=n)]
    use_alternate = rng.random(n) < ALTERNATE_SUPPLIER_SHARE
    target = materials["spec_target"].to_numpy()[material_idx]
    tolerance = materials["spec_tolerance"].to_numpy()[material_idx]
    frame = pd.DataFrame(
        {
            "material_id": materials["material_id"].to_numpy()[material_idx],
            "supplier_id": np.where(use_alternate, alternate, primary),
            "received_date": _dates(config.start, received),
            "quantity_kg": np.round(rng.uniform(200.0, 2000.0, size=n), 1),
            "spec_measurement": np.round(
                target + rng.normal(0.0, 1.0, size=n) * tolerance / 2.5, 4
            ),
            "certified": rng.random(n) < CERTIFIED_SHARE,
        }
    )
    ordered = frame.sort_values(["received_date", "material_id"], kind="stable")
    ordered = ordered.reset_index(drop=True)
    return ordered.assign(lot_id=make_ids("LOT", n, 6))[["lot_id", *frame.columns]]


def build_operators(config: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    """Operators with role, home line and shift, hire and certification dates."""
    n = config.operators
    hire_offset = -rng.integers(30, 10 * DAYS_PER_YEAR, size=n)
    training_lag = rng.integers(14, 120, size=n)
    certified = _dates(config.start, hire_offset + training_lag)
    uncertified = rng.random(n) < UNCERTIFIED_SHARE
    role = np.select(
        [rng.random(n) < INSPECTOR_SHARE, rng.random(n) < TECHNICIAN_SHARE],
        ["inspector", "technician"],
        default="operator",
    ).astype(str)
    shifts = np.array(SHIFT_NAMES, dtype=str)
    return pd.DataFrame(
        {
            "operator_id": make_ids("O", n, 3),
            "hire_date": _dates(config.start, hire_offset),
            "training_certified_date": pd.Series(certified).mask(uncertified),
            "role": role,
            "home_line_id": make_ids("L", config.lines, 2)[rng.integers(0, config.lines, size=n)],
            "home_shift": shifts[rng.integers(0, len(shifts), size=n)],
        }
    )


def build_shift_assignments(
    config: GeneratorConfig, rng: np.random.Generator, operators: pd.DataFrame
) -> pd.DataFrame:
    """One roster row per operator per day; a tenth of days swap the line."""
    n_ops = len(operators)
    n = n_ops * config.days
    day = np.repeat(np.arange(config.days), n_ops)
    op_idx = np.tile(np.arange(n_ops), config.days)
    home_line = operators["home_line_id"].to_numpy()[op_idx]
    swapped = make_ids("L", config.lines, 2)[rng.integers(0, config.lines, size=n)]
    use_swap = rng.random(n) < LINE_SWAP_SHARE
    return pd.DataFrame(
        {
            "date": _dates(config.start, day),
            "operator_id": operators["operator_id"].to_numpy()[op_idx],
            "line_id": np.where(use_swap, swapped, home_line),
            "shift": operators["home_shift"].to_numpy()[op_idx],
        }
    )


def build_dimensions(config: GeneratorConfig, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Build every dimension table in a fixed order (order matters for determinism)."""
    plants = build_plants(config)
    lines = build_lines(config, rng)
    machines = build_machines(config, rng, lines)
    families = build_product_families(config)
    products = build_products(config, rng)
    suppliers = build_suppliers(config, rng)
    materials = build_materials(config, rng)
    lots = build_material_lots(config, rng, materials)
    operators = build_operators(config, rng)
    assignments = build_shift_assignments(config, rng, operators)
    return {
        "plants": plants,
        "production_lines": lines,
        "machines": machines,
        "product_families": families,
        "products": products,
        "suppliers": suppliers,
        "materials": materials,
        "material_lots": lots,
        "operators": operators,
        "shift_assignments": assignments,
    }
