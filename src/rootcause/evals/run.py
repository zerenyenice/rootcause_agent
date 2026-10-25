"""Command line: ``python -m rootcause.evals.run --cases DIR/cases.json --agent mod:func``.

Exit codes: 0 when every selected case passed, 1 when any failed, 2 for usage errors
(bad arguments, unreadable cases file, unknown level, agent that cannot be imported).
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path

from rootcause.evals.manufacturing.incidents.manifest import CaseManifest
from rootcause.evals.runner import Investigate, load_cases, run_cases

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
ALL_LEVELS = "all"


class UsageError(ValueError):
    """Invalid command line input; reported on stderr with exit code 2."""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m rootcause.evals.run",
        description="Run the root-cause agent over planted cases and score it (issue #45).",
    )
    parser.add_argument("--cases", type=Path, required=True, help="cases.json from the generator")
    parser.add_argument(
        "--levels", default=ALL_LEVELS, help="comma-separated levels such as easy, or all"
    )
    parser.add_argument("--agent", required=True, help="investigate callable as module:function")
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    return parser


def load_agent(spec: str) -> Investigate:
    """Import the ``module:function`` callable; ``UsageError`` if it cannot be resolved."""
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise UsageError(f"--agent must look like module:function, got {spec!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise UsageError(f"cannot import agent module {module_name!r}: {exc}") from exc
    target = getattr(module, attr, None)
    if not callable(target):
        raise UsageError(f"{module_name!r} has no callable {attr!r}")
    agent: Investigate = target
    return agent


def select_levels(cases: tuple[CaseManifest, ...], levels: str) -> tuple[CaseManifest, ...]:
    """Cases at the requested levels; ``UsageError`` for an empty or unmatched level."""
    if levels.strip() == ALL_LEVELS:
        wanted = tuple(dict.fromkeys(case.level for case in cases))
    else:
        wanted = tuple(part.strip() for part in levels.split(",") if part.strip())
    present = {case.level for case in cases}
    missing = [level for level in wanted if level not in present]
    if missing or not wanted:
        raise UsageError(f"no cases at level(s) {missing or [levels]}; present: {sorted(present)}")
    return tuple(case for case in cases if case.level in wanted)


def _load(path: Path) -> tuple[CaseManifest, ...]:
    """Load the cases file, turning read and parse failures into ``UsageError``."""
    try:
        return load_cases(path)
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot load cases from {path}: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv``, run the evaluation and return the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        cases = select_levels(_load(args.cases), args.levels)
        investigate = load_agent(args.agent)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = run_cases(cases, investigate)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report.to_json(), encoding="utf-8")
    print(report.to_markdown(), end="")
    return EXIT_PASSED if report.all_passed else EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - exercised via ``main`` in tests
    sys.exit(main(sys.argv[1:]))
