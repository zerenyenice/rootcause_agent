"""Repository-level tests: one installable ``rootcause`` package with a src layout."""

import importlib
import tomllib
from pathlib import Path
from typing import Any

import pytest

# Subpackages of the single ``rootcause`` distribution, with the tests/ area for each.
SUBPACKAGES = ["agents", "api", "catalog", "connections", "db", "evals"]
MODULE_NAMES = ["rootcause", *(f"rootcause.{name}" for name in SUBPACKAGES)]
SMOKE_TEST_AREAS = ["core", "agents", "api", "catalog", "connections", "evals"]


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
def test_root_pyproject_declares_the_single_rootcause_package(repo_root: Path) -> None:
    data = _load_root_pyproject(repo_root)

    assert data["project"]["name"] == "rootcause"
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/rootcause"]
    assert "workspace" not in data["tool"].get("uv", {})


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
    "relative_file",
    [
        "pyproject.toml",
        "src/rootcause/__init__.py",
        "src/rootcause/py.typed",
        *(f"src/rootcause/{name}/__init__.py" for name in SUBPACKAGES),
        *(f"tests/{area}/test_smoke.py" for area in SMOKE_TEST_AREAS),
    ],
)
def test_package_has_required_file(repo_root: Path, relative_file: str) -> None:
    target = repo_root / relative_file

    assert target.is_file(), f"missing {relative_file}"


@pytest.mark.unit
@pytest.mark.parametrize("legacy_dir", ["apps", "packages"])
def test_legacy_workspace_directories_are_gone(repo_root: Path, legacy_dir: str) -> None:
    assert not (repo_root / legacy_dir).exists()


@pytest.mark.unit
def test_python_version_file_pins_312(repo_root: Path) -> None:
    path = repo_root / ".python-version"

    assert path.is_file(), ".python-version is missing"
    assert path.read_text().strip() == "3.12"


@pytest.mark.unit
@pytest.mark.parametrize("placeholder", ["web/README.md", "deploy/README.md"])
def test_placeholder_readme_exists(repo_root: Path, placeholder: str) -> None:
    assert (repo_root / placeholder).is_file()


@pytest.mark.unit
@pytest.mark.parametrize("command", ["uv sync", "uv run pytest"])
def test_root_readme_documents_command(repo_root: Path, command: str) -> None:
    readme = repo_root / "README.md"

    assert readme.is_file()
    assert command in readme.read_text()
