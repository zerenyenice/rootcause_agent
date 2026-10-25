"""Workspace-level tests for the T001 monorepo scaffold."""

import importlib
import tomllib
from pathlib import Path
from typing import Any

import pytest

# (directory relative to repo root, distribution/module name)
PACKAGES = [
    ("apps/api", "rootcause_api"),
    ("packages/core", "rootcause_core"),
    ("packages/agents", "rootcause_agents"),
    ("packages/tools", "rootcause_tools"),
    ("packages/catalog", "rootcause_catalog"),
    ("packages/connections", "rootcause_connections"),
    ("packages/anomaly", "rootcause_anomaly"),
    ("packages/memory", "rootcause_memory"),
    ("packages/evals", "rootcause_evals"),
]
MODULE_NAMES = [name for _, name in PACKAGES]


def _load_root_pyproject(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "pyproject.toml"
    assert path.is_file(), "root pyproject.toml is missing"
    with path.open("rb") as fh:
        return tomllib.load(fh)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_package_is_importable_and_exposes_version(module_name: str) -> None:
    module = importlib.import_module(module_name)

    assert module.__version__ == "0.1.0"


@pytest.mark.unit
def test_root_pyproject_declares_workspace_members(repo_root: Path) -> None:
    data = _load_root_pyproject(repo_root)

    members = data["tool"]["uv"]["workspace"]["members"]

    assert "apps/api" in members
    assert "packages/*" in members


@pytest.mark.unit
def test_root_pyproject_requires_python_312_or_newer(repo_root: Path) -> None:
    data = _load_root_pyproject(repo_root)

    assert data["project"]["requires-python"].startswith(">=3.12")


@pytest.mark.unit
def test_root_pytest_config_enforces_coverage_gate(repo_root: Path) -> None:
    data = _load_root_pyproject(repo_root)

    addopts = data["tool"]["pytest"]["ini_options"]["addopts"]
    addopts_text = addopts if isinstance(addopts, str) else " ".join(addopts)

    assert "--cov-fail-under=80" in addopts_text


@pytest.mark.unit
def test_root_pytest_config_registers_test_markers(repo_root: Path) -> None:
    data = _load_root_pyproject(repo_root)

    markers = data["tool"]["pytest"]["ini_options"]["markers"]
    marker_names = {m.split(":", 1)[0].strip() for m in markers}

    assert {"unit", "integration", "e2e"} <= marker_names


@pytest.mark.unit
@pytest.mark.parametrize(
    ("directory", "dist"),
    PACKAGES,
    ids=MODULE_NAMES,
)
@pytest.mark.parametrize(
    "relative_file",
    [
        "pyproject.toml",
        "src/{dist}/__init__.py",
        "src/{dist}/py.typed",
        "tests/test_smoke.py",
    ],
)
def test_package_has_required_file(
    repo_root: Path, directory: str, dist: str, relative_file: str
) -> None:
    target = repo_root / directory / relative_file.format(dist=dist)

    assert target.is_file(), f"missing {target.relative_to(repo_root)}"


@pytest.mark.unit
def test_python_version_file_pins_312(repo_root: Path) -> None:
    path = repo_root / ".python-version"

    assert path.is_file(), ".python-version is missing"
    assert path.read_text().strip() == "3.12"


@pytest.mark.unit
@pytest.mark.parametrize("placeholder", ["apps/web/README.md", "deploy/README.md"])
def test_placeholder_readme_exists(repo_root: Path, placeholder: str) -> None:
    assert (repo_root / placeholder).is_file()


@pytest.mark.unit
@pytest.mark.parametrize("command", ["uv sync", "uv run pytest"])
def test_root_readme_documents_command(repo_root: Path, command: str) -> None:
    readme = repo_root / "README.md"

    assert readme.is_file()
    assert command in readme.read_text()
