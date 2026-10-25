"""Command line: ``python -m rootcause.evals.run --cases DIR/cases.json --agent mod:func``.

Exit codes: 0 when every selected case passed, 1 when any failed, 2 for usage errors
(bad arguments, unreadable cases file, unknown level or ``all`` mixed with levels, agent
module that fails to import for any reason, ``--out`` that cannot be written).
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
    except Exception as exc:  # noqa: BLE001 - any import-time failure is a usage error
        raise UsageError(
            f"cannot import agent module {module_name!r}: {type(exc).__name__}: {exc}"
        ) from exc
    target = getattr(module, attr, None)
    if not callable(target):
        raise UsageError(f"{module_name!r} has no callable {attr!r}")
    agent: Investigate = target
    return agent


def select_levels(cases: tuple[CaseManifest, ...], levels: str) -> tuple[CaseManifest, ...]:
    """Cases at the requested levels.

    ``UsageError`` for an empty or unmatched level, or ``all`` combined with other levels.
    """
    parts = tuple(dict.fromkeys(part.strip() for part in levels.split(",") if part.strip()))
    if ALL_LEVELS in parts and len(parts) > 1:
        raise UsageError(
            f"--levels {ALL_LEVELS!r} cannot be combined with other levels: {levels!r}"
        )
    all_present = tuple(dict.fromkeys(case.level for case in cases))
    wanted = all_present if parts == (ALL_LEVELS,) else parts
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


def _write_report(path: Path, content: str) -> None:
    """Write the JSON report, turning file system failures into ``UsageError``."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot write report to {path}: {exc}") from exc


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
    print(report.to_markdown(), end="")
    if args.out is not None:
        try:
            _write_report(args.out, report.to_json())
        except UsageError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    return EXIT_PASSED if report.all_passed else EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - exercised via ``main`` in tests
    sys.exit(main(sys.argv[1:]))
