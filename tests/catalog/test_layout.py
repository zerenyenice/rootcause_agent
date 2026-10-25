"""rootcause.catalog ships in the rootcause package and loads rootcause.connections."""

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_catalog_depends_on_connections():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())

    assert pyproject["project"]["name"] == "rootcause"
    for subpackage in ("catalog", "connections"):
        assert (REPO_ROOT / "src" / "rootcause" / subpackage / "__init__.py").is_file()


def test_importing_only_catalog_models_leaves_mappers_configurable():
    code = (
        "import rootcause.catalog.models\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "configure_mappers()\n"
        "from rootcause.db.base import Base\n"
        "assert 'app.connections' in Base.metadata.tables\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )

    assert result.returncode == 0, result.stderr
