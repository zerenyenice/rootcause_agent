"""Catalog fixtures: in-memory repositories, a small DuckDB warehouse and test settings."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

TEST_DATABASE_URL = "postgresql://u:p@127.0.0.1:1/db"


@pytest.fixture(autouse=True)
def _clean_settings_cache() -> Iterator[None]:
    from rootcause.settings import clear_settings_cache

    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def secret_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


@pytest.fixture
def settings(secret_key: str, make_embeddings: Callable[..., Any]) -> Any:
    """Test settings: 8-dimensional embeddings (the fake's size) and a Fernet key."""
    from rootcause.settings import Settings

    return Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        environment="test",
        secret_key=secret_key,
        embedding_dimensions=make_embeddings().dimensions,
    )


@pytest.fixture
def connections() -> Any:
    from rootcause.connections.repository import InMemoryConnectionRepository

    return InMemoryConnectionRepository()


@pytest.fixture
def catalog() -> Any:
    from rootcause.catalog.repository import InMemoryCatalogRepository

    return InMemoryCatalogRepository()


@pytest.fixture
def embedder(make_embeddings: Callable[..., Any]) -> Any:
    return make_embeddings()


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    """``plants`` (3 rows) and ``production`` (5 rows, scrap counts) in schema ``main``."""
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE plants (plant_id VARCHAR NOT NULL, plant_name VARCHAR)")
        con.execute("INSERT INTO plants VALUES ('P01','Alpha'),('P02','Beta'),('P03','Gamma')")
        con.execute("CREATE TABLE production (ts INTEGER, units INTEGER NOT NULL, scrap INTEGER)")
        con.execute("INSERT INTO production SELECT i, i * 10, i FROM range(5) t(i)")
    finally:
        con.close()
    return path
