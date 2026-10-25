"""Alembic environment files ship with core, import cleanly and form a chain."""

import importlib.util
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

import rootcause_core

CORE_ROOT = Path(rootcause_core.__file__).parent
ALEMBIC_DIR = CORE_ROOT / "db" / "alembic"
VERSIONS_DIR = ALEMBIC_DIR / "versions"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version_files() -> list[Path]:
    return sorted(p for p in VERSIONS_DIR.glob("*.py") if not p.name.startswith("__"))


def test_alembic_environment_files_exist():
    assert (ALEMBIC_DIR / "env.py").is_file()
    assert (ALEMBIC_DIR / "script.py.mako").is_file()
    assert (REPO_ROOT / "deploy" / "alembic.ini").is_file()


def test_initial_migration_exists():
    assert any(p.name.startswith("0001_") for p in _version_files())


def test_every_migration_has_revision_and_down_revision():
    files = _version_files()
    assert files, "no migration scripts found"

    for path in files:
        module = _load(path)
        assert isinstance(module.revision, str)
        assert module.revision
        assert hasattr(module, "down_revision")
        assert callable(module.upgrade)
        assert callable(module.downgrade)


def test_migrations_form_a_single_linear_chain_from_the_initial_revision():
    modules = [_load(p) for p in _version_files()]
    revisions = {m.revision for m in modules}

    roots = [m for m in modules if m.down_revision is None]
    assert len(roots) == 1
    assert len(revisions) == len(modules), "duplicate revision ids"
    for module in modules:
        assert module.down_revision is None or module.down_revision in revisions


def test_migrate_helpers_have_the_pinned_names():
    from rootcause_core.db import migrate

    assert callable(migrate.upgrade)
    assert callable(migrate.downgrade)


def test_rootcause_db_console_script_is_declared_and_loadable():
    scripts = {
        ep.name: ep
        for ep in metadata.entry_points(group="console_scripts")
        if ep.dist is not None and ep.dist.name == "rootcause-core"
    }

    assert "rootcause-db" in scripts
    assert callable(scripts["rootcause-db"].load())


def test_core_declares_runtime_database_dependencies():
    pyproject = tomllib.loads((CORE_ROOT.parents[1] / "pyproject.toml").read_text())
    dependencies = " ".join(pyproject["project"]["dependencies"]).lower()

    for package in ("sqlalchemy", "alembic", "psycopg", "asyncpg"):
        assert package in dependencies


@pytest.mark.parametrize("subcommand", ["upgrade", "downgrade", "current", "revision"])
def test_cli_exposes_the_documented_subcommands(subcommand):
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
