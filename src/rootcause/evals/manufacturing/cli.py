"""Command line entry point: ``python -m rootcause.evals.manufacturing generate``."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from rootcause.evals.manufacturing.config import GeneratorConfig
from rootcause.evals.manufacturing.incidents import (
    ARCHETYPES,
    Incident,
    assert_visible,
    instantiate,
    write_cases_json,
)
from rootcause.evals.manufacturing.writers import (
    write_csv,
    write_data_dictionary,
    write_duckdb,
    write_postgres_sql,
)

EXIT_OK = 0
EXIT_INVALID_CONFIG = 2
EXIT_NOT_VISIBLE = 3
CASES_FILENAME = "cases.json"
DUCKDB_FILENAME = "warehouse.duckdb"
SQL_FILENAME = "warehouse.sql"
CSV_DIRNAME = "csv"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the ``generate`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="python -m rootcause.evals.manufacturing",
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
    generate.add_argument(
        "--incidents",
        default="none",
        help="planted incidents: all, none or a comma-separated archetype list such as E1,E2",
    )
    generate.add_argument(
        "--no-visibility-check",
        action="store_true",
        help="report incident visibility but do not fail when an incident is not visible",
    )
    return parser


def parse_incident_selection(value: str) -> tuple[str, ...]:
    """Archetype ids for ``--incidents`` (``all``, ``none`` or ``E1,E2``); ValueError if unknown."""
    text = value.strip()
    if text == "all":
        return tuple(ARCHETYPES)
    if text == "none":
        return ()
    ids = tuple(part.strip() for part in text.split(",") if part.strip())
    unknown = [i for i in ids if i not in ARCHETYPES]
    if unknown or not ids:
        raise ValueError(
            f"unknown incident archetype(s) {unknown or [value]}; known: {sorted(ARCHETYPES)}"
        )
    return ids


def write_outputs(tables: dict[str, pd.DataFrame], out_dir: Path) -> None:
    """Write CSVs, the DuckDB file, the Postgres dump and the data dictionary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(tables, out_dir / CSV_DIRNAME)
    write_duckdb(tables, out_dir / DUCKDB_FILENAME)
    write_postgres_sql(tables, out_dir / SQL_FILENAME)
    write_data_dictionary(out_dir)


def report_visibility(
    tables: dict[str, pd.DataFrame], incidents: tuple[Incident, ...], enforce: bool
) -> int:
    """Print the robust z of every incident; nonzero exit code if enforced and one is hidden."""
    code = EXIT_OK
    for incident in incidents:
        report = assert_visible(tables, incident)
        print(f"visibility {report.case_id}: z={report.z:.1f} visible={report.visible}")
        if enforce and not report.visible:
            print(f"error: incident {report.case_id} is not visible", file=sys.stderr)
            code = EXIT_NOT_VISIBLE
    return code


def run_generate(
    config: GeneratorConfig,
    out_dir: Path,
    archetypes: tuple[str, ...] = (),
    enforce_visibility: bool = True,
) -> int:
    """Generate the warehouse, write all outputs and print a per-table summary."""
    from rootcause.evals.manufacturing import generate

    started = time.perf_counter()
    incidents = instantiate(config, archetypes=archetypes) if archetypes else ()
    tables = generate(config, incidents, check_visibility=False)
    generated = time.perf_counter() - started
    code = report_visibility(tables, incidents, enforce_visibility)
    if code != EXIT_OK:
        return code
    write_outputs(tables, out_dir)
    if incidents:
        write_cases_json([i.manifest_entry(tables) for i in incidents], out_dir / CASES_FILENAME)
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
    try:
        archetypes = parse_incident_selection(args.incidents)
        return run_generate(config, args.out, archetypes, not args.no_visibility_check)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID_CONFIG
