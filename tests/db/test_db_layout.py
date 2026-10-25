"""The foundation layer keeps Base, schema constants and engine factories only.

Foundation = the top-level ``rootcause`` modules, ``rootcause.clients``,
``rootcause.observability`` and ``rootcause.db``'s base/engine/schemas modules. The
migration machinery lives beside them in ``rootcause.db`` but the foundation never
imports the domain packages it serves.
"""

import ast
from pathlib import Path

import pytest

import rootcause

PACKAGE_SRC = Path(rootcause.__file__).parent
DB_SRC = PACKAGE_SRC / "db"
FOUNDATION_DB_MODULES = ("__init__.py", "base.py", "engine.py", "schemas.py")
FORBIDDEN_IMPORTS = ("rootcause.connections", "rootcause.catalog")


def _foundation_files() -> list[Path]:
    return [
        *PACKAGE_SRC.glob("*.py"),
        *(PACKAGE_SRC / "clients").rglob("*.py"),
        *(PACKAGE_SRC / "observability").rglob("*.py"),
        *(DB_SRC / name for name in FOUNDATION_DB_MODULES),
    ]


@pytest.mark.parametrize("name", ["alembic", "migrate.py", "cli.py"])
def test_migration_machinery_lives_in_the_db_package(name):
    assert (DB_SRC / name).exists()


def test_db_console_script_has_a_single_owner():
    from importlib import metadata

    owners = [
        ep.dist.name
        for ep in metadata.entry_points(group="console_scripts", name="rootcause-db")
        if ep.dist is not None
    ]

    assert owners == ["rootcause"]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _is_forbidden(module: str) -> bool:
    return any(module == f or module.startswith(f"{f}.") for f in FORBIDDEN_IMPORTS)


def test_core_source_never_imports_sibling_domain_packages():
    offenders = {
        str(path.relative_to(PACKAGE_SRC)): sorted(
            m for m in _imported_modules(path) if _is_forbidden(m)
        )
        for path in _foundation_files()
    }

    assert {k: v for k, v in offenders.items() if v} == {}


def test_core_still_exposes_base_schemas_and_engines():
    from rootcause.db.base import Base
    from rootcause.db.engine import make_async_engine, make_engine
    from rootcause.db.schemas import ALL_SCHEMAS

    assert Base is not None
    assert callable(make_engine) and callable(make_async_engine)
    assert ALL_SCHEMAS == ("app", "catalog", "audit")
