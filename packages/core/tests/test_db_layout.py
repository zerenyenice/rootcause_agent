"""rootcause-core keeps Base, schema constants and engine factories only."""

import ast
from pathlib import Path

import pytest

import rootcause_core

CORE_SRC = Path(rootcause_core.__file__).parent
FORBIDDEN_IMPORTS = ("rootcause_connections", "rootcause_catalog")


@pytest.mark.parametrize("name", ["alembic", "migrate.py", "cli.py"])
def test_migration_machinery_moved_out_of_core(name):
    assert not (CORE_SRC / "db" / name).exists()


def test_core_has_no_rootcause_db_console_script():
    from importlib import metadata

    owners = [
        ep
        for ep in metadata.entry_points(group="console_scripts", name="rootcause-db")
        if ep.dist is not None and ep.dist.name == "rootcause-core"
    ]

    assert owners == []


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_core_source_never_imports_sibling_domain_packages():
    offenders = {
        str(path.relative_to(CORE_SRC)): sorted(
            m for m in _imported_modules(path) if m.split(".")[0] in FORBIDDEN_IMPORTS
        )
        for path in CORE_SRC.rglob("*.py")
    }

    assert {k: v for k, v in offenders.items() if v} == {}


def test_core_still_exposes_base_schemas_and_engines():
    from rootcause_core.db.base import Base
    from rootcause_core.db.engine import make_async_engine, make_engine
    from rootcause_core.db.schemas import ALL_SCHEMAS

    assert Base is not None
    assert callable(make_engine) and callable(make_async_engine)
    assert ALL_SCHEMAS == ("app", "catalog", "audit")
