"""Command line interface for the warehouse generator."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_generate_command_writes_all_outputs_and_prints_row_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from rootcause_evals.manufacturing.cli import main

    code = main(["generate", "--out", str(tmp_path), "--days", "7", "--seed", "3"])

    output = capsys.readouterr().out
    assert code == 0
    assert "production_runs" in output
    assert str(8 * 24 * 7) in output
    assert (tmp_path / "warehouse.duckdb").is_file()
    assert (tmp_path / "csv" / "production_runs.csv").is_file()
    assert (tmp_path / "warehouse.sql").is_file()
    assert (tmp_path / "DATA_DICTIONARY.md").is_file()


@pytest.mark.unit
def test_invalid_days_exits_nonzero(tmp_path: Path) -> None:
    from rootcause_evals.manufacturing.cli import main

    try:
        code = main(["generate", "--out", str(tmp_path), "--days", "0"])
    except SystemExit as exc:
        code = exc.code

    assert code not in (0, None)
