"""Command line entry point: ``python -m rootcause_evals.manufacturing generate``."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from rootcause_evals.manufacturing.config import GeneratorConfig
from rootcause_evals.manufacturing.writers import (
    write_csv,
    write_data_dictionary,
    write_duckdb,
    write_postgres_sql,
)

EXIT_OK = 0
EXIT_INVALID_CONFIG = 2
DUCKDB_FILENAME = "warehouse.duckdb"
SQL_FILENAME = "warehouse.sql"
CSV_DIRNAME = "csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rootcause_evals.manufacturing",
        description="Generate the synthetic manufacturing warehouse (PRD section 11).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="generate every output into a directory")
    generate.add_argument("--out", type=Path, required=True, help="output directory")
    defaults = GeneratorConfig()
    generate.add_argument("--days", type=int, default=defaults.days, help="days of data")
    generate.add_argument("--seed", type=int, default=defaults.seed, help="random seed")
    generate.add_argument(
        "--scale", type=int, default=defaults.scale, help="1 (hourly) or 10 (10-minute grain)"
    )
    return parser


def write_outputs(tables: dict[str, pd.DataFrame], out_dir: Path) -> None:
    """Write CSVs, the DuckDB file, the Postgres dump and the data dictionary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(tables, out_dir / CSV_DIRNAME)
    write_duckdb(tables, out_dir / DUCKDB_FILENAME)
    write_postgres_sql(tables, out_dir / SQL_FILENAME)
    write_data_dictionary(out_dir)


def run_generate(config: GeneratorConfig, out_dir: Path) -> int:
    from rootcause_evals.manufacturing import generate

    started = time.perf_counter()
    tables = generate(config)
    generated = time.perf_counter() - started
    write_outputs(tables, out_dir)
    total = time.perf_counter() - started
    width = max(len(name) for name in tables)
    for name, frame in tables.items():
        print(f"{name:<{width}}  {len(frame):>10d}")
    print(f"generated in {generated:.1f}s, written to {out_dir} in {total:.1f}s total")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the requested command; returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        config = GeneratorConfig(seed=args.seed, days=args.days, scale=args.scale)
    except ValueError as exc:
        print(f"error: invalid configuration: {exc}", file=sys.stderr)
        return EXIT_INVALID_CONFIG
    return run_generate(config, args.out)
