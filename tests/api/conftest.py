"""Shared fixtures: isolated ROOTCAUSE_* environment and an app factory."""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://u:p@127.0.0.1:1/db"


def _clear_settings_cache() -> None:
    from rootcause.settings import clear_settings_cache

    clear_settings_cache()


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in [key for key in os.environ if key.upper().startswith("ROOTCAUSE_")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # no stray .env file is picked up
    _clear_settings_cache()
    yield
    _clear_settings_cache()


@pytest.fixture(autouse=True)
def reset_logging_state() -> Iterator[None]:
    yield
    import structlog

    structlog.reset_defaults()


@pytest.fixture
def make_app(monkeypatch: pytest.MonkeyPatch) -> Callable[..., "FastAPI"]:
    """Build an app from env; keyword overrides map to ``ROOTCAUSE_<NAME>``."""

    def _make(**settings_overrides: str) -> "FastAPI":
        monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", UNREACHABLE_DATABASE_URL)
        for name, value in settings_overrides.items():
            monkeypatch.setenv(f"ROOTCAUSE_{name.upper()}", value)
        _clear_settings_cache()
        from rootcause.api.main import create_app

        return create_app()

    return _make


@pytest.fixture
def make_client(make_app: Callable[..., "FastAPI"]) -> Callable[..., "TestClient"]:
    """Like ``make_app`` but returns a TestClient (lazy fastapi import)."""

    def _make(**settings_overrides: str) -> "TestClient":
        from fastapi.testclient import TestClient

        return TestClient(make_app(**settings_overrides))

    return _make


# --- connection management fixtures (feat/connections-api) -------------------

CONNECTIONS_PATH = "/api/v1/connections"
SENTINEL_PASSWORD = "SENTINEL-pw-123"  # gitleaks:allow
_DROP = object()


@pytest.fixture
def secret_key() -> str:
    """A fresh Fernet key per test; never a literal in the repository."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


@pytest.fixture
def connection_repository() -> Any:
    """In-memory repository the connection routes are pointed at (no Postgres)."""
    from rootcause.connections.repository import InMemoryConnectionRepository

    return InMemoryConnectionRepository()


@pytest.fixture
def make_connections_app(
    make_app: Callable[..., "FastAPI"], connection_repository: Any
) -> Callable[..., "FastAPI"]:
    """App whose connection routes use ``connection_repository``."""

    def _make(**settings_overrides: str) -> "FastAPI":
        from rootcause.api.routes.connections import get_connection_repository

        app = make_app(**settings_overrides)
        app.dependency_overrides[get_connection_repository] = lambda: connection_repository
        return app

    return _make


@pytest.fixture
def connections_client(
    make_connections_app: Callable[..., "FastAPI"], secret_key: str
) -> Iterator["TestClient"]:
    from fastapi.testclient import TestClient

    with TestClient(make_connections_app(secret_key=secret_key)) as client:
        yield client


@pytest.fixture
def postgres_body() -> Callable[..., dict[str, Any]]:
    """Factory for a valid postgres create body; keyword overrides replace fields."""

    def _make(**overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": "plant warehouse",
            "kind": "postgres",
            "host": "127.0.0.1",
            "port": 1,
            "database": "warehouse",
            "user": "analyst",
            "password": SENTINEL_PASSWORD,
        }
        body.update(overrides)
        return {key: value for key, value in body.items() if value is not _DROP}

    return _make


@pytest.fixture
def drop() -> object:
    """Marker: pass as an override value to remove that field from a body."""
    return _DROP


@pytest.fixture
def warehouse_file(tmp_path: Path) -> Path:
    """A small DuckDB file: ``plants`` (3 rows, NOT NULL key) and ``production`` (5 rows)."""
    import duckdb

    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE plants (plant_id VARCHAR NOT NULL, plant_name VARCHAR)")
        con.execute("INSERT INTO plants VALUES ('P01','Alpha'),('P02','Beta'),('P03','Gamma')")
        con.execute("CREATE TABLE production (ts INTEGER, units INTEGER NOT NULL)")
        con.execute("INSERT INTO production SELECT i, i FROM range(5) t(i)")
    finally:
        con.close()
    return path


def _find_forbidden_keys(value: Any, path: str = "") -> list[str]:
    """Paths of every key mentioning password/secret/encrypted, at any depth."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}"
            if any(word in str(key).lower() for word in ("password", "secret", "encrypted")):
                found.append(here)
            found.extend(_find_forbidden_keys(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_forbidden_keys(item, f"{path}[{index}]"))
    return found


@pytest.fixture
def forbidden_keys() -> Callable[[Any], list[str]]:
    """``forbidden_keys(body)`` lists every key path naming a password/secret/encrypted value."""
    return _find_forbidden_keys


# --- metadata catalog fixtures (feat/catalog) ---------------------------------

CATALOG_PATH = "/api/v1/catalog"


@pytest.fixture
def catalog_repository() -> Any:
    """In-memory catalog repository the catalog routes are pointed at (no Postgres)."""
    from rootcause.catalog.repository import InMemoryCatalogRepository

    return InMemoryCatalogRepository()


@pytest.fixture
def catalog_embeddings(make_embeddings: Callable[..., Any]) -> Any:
    """The fake embeddings client the catalog routes use; set ``.error`` to make it fail."""
    return make_embeddings()


@pytest.fixture
def catalog_client(
    make_app: Callable[..., "FastAPI"],
    connection_repository: Any,
    catalog_repository: Any,
    catalog_embeddings: Any,
    secret_key: str,
) -> Iterator["TestClient"]:
    """Client over an app whose connection and catalog routes use in-memory repositories."""
    from fastapi.testclient import TestClient

    from rootcause.api.routes.catalog import get_catalog_repository, get_embeddings
    from rootcause.api.routes.connections import get_connection_repository

    app = make_app(secret_key=secret_key, embedding_dimensions=str(catalog_embeddings.dimensions))
    app.dependency_overrides[get_connection_repository] = lambda: connection_repository
    app.dependency_overrides[get_catalog_repository] = lambda: catalog_repository
    app.dependency_overrides[get_embeddings] = lambda: catalog_embeddings
    with TestClient(app) as client:
        yield client
