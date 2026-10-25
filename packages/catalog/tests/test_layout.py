"""rootcause-catalog declares and loads its dependency on rootcause-connections."""

import subprocess
import sys
import tomllib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_catalog_depends_on_connections():
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())

    assert "rootcause-connections" in " ".join(pyproject["project"]["dependencies"])
    assert pyproject["tool"]["uv"]["sources"]["rootcause-connections"] == {"workspace": True}


def test_importing_only_catalog_models_leaves_mappers_configurable():
    code = (
        "import rootcause_catalog.models\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "configure_mappers()\n"
        "from rootcause_core.db.base import Base\n"
        "assert 'app.connections' in Base.metadata.tables\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )

    assert result.returncode == 0, result.stderr
