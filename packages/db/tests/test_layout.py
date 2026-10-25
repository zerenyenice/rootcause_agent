"""rootcause-db owns the Alembic environment, migrate helpers, CLI and bootstrap."""

import importlib.util
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
ALEMBIC_DIR = PACKAGE_ROOT / "src" / "rootcause_db" / "alembic"
VERSIONS_DIR = ALEMBIC_DIR / "versions"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version_files() -> list[Path]:
    return sorted(p for p in VERSIONS_DIR.glob("*.py") if not p.name.startswith("__"))


def test_public_entry_points_are_importable():
    from rootcause_db.bootstrap import bootstrap
    from rootcause_db.cli import main
    from rootcause_db.migrate import downgrade, upgrade

    for function in (bootstrap, main, downgrade, upgrade):
        assert callable(function)


def test_alembic_environment_files_exist():
    assert (ALEMBIC_DIR / "env.py").is_file()
    assert (ALEMBIC_DIR / "script.py.mako").is_file()


def test_alembic_ini_points_at_the_db_package():
    text = (REPO_ROOT / "deploy" / "alembic.ini").read_text()

    assert "packages/db/src/rootcause_db/alembic" in text
    assert "rootcause_core" not in text


def test_initial_and_audit_trigger_migrations_exist():
    names = [p.name for p in _version_files()]

    assert any(n.startswith("0001_") for n in names)
    assert any(n.startswith("0002_") for n in names)


def test_migrations_form_a_single_linear_chain():
    modules = [_load(p) for p in _version_files()]
    revisions = {m.revision for m in modules}

    assert len(revisions) == len(modules), "duplicate revision ids"
    roots = [m for m in modules if m.down_revision is None]
    assert len(roots) == 1
    parents = [m.down_revision for m in modules if m.down_revision is not None]
    assert len(parents) == len(set(parents)), "branching: two revisions share a parent"
    for module in modules:
        assert module.down_revision is None or module.down_revision in revisions
        assert callable(module.upgrade)
        assert callable(module.downgrade)


def test_0002_follows_0001():
    by_prefix = {p.name[:4]: _load(p) for p in _version_files()}

    assert by_prefix["0002"].down_revision == by_prefix["0001"].revision


def test_rootcause_db_console_script_is_declared_in_this_package():
    scripts = {
        ep.name: ep
        for ep in metadata.entry_points(group="console_scripts")
        if ep.dist is not None and ep.dist.name == "rootcause-db"
    }

    assert "rootcause-db" in scripts
    assert scripts["rootcause-db"].value == "rootcause_db.cli:main"
    assert callable(scripts["rootcause-db"].load())


def test_package_depends_on_core_connections_and_catalog():
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    dependencies = " ".join(pyproject["project"]["dependencies"]).lower()

    for package in ("rootcause-core", "rootcause-connections", "rootcause-catalog", "alembic"):
        assert package in dependencies


@pytest.mark.parametrize("subcommand", ["upgrade", "downgrade", "current", "revision", "bootstrap"])
def test_cli_exposes_the_documented_subcommands(subcommand):
    import subprocess
    import sys

    executable = Path(sys.executable).parent / "rootcause-db"
    assert executable.is_file(), "rootcause-db console script is not installed"

    result = subprocess.run(
        [str(executable), subcommand, "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert subcommand in result.stdout
