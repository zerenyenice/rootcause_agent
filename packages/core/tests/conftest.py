"""Shared fixtures: isolate env vars, settings cache, structlog and OTel globals."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

DB_URL = "postgresql://user:s3cr3t-pw@localhost:5432/rootcause"


def _clear_settings_cache_if_available() -> None:
    try:
        from rootcause_core.settings import clear_settings_cache
    except ImportError:
        return
    clear_settings_cache()


def _reset_otel_globals() -> None:
    """Allow a fresh global TracerProvider to be installed in the next test."""
    try:
        from opentelemetry import trace
    except ImportError:
        return
    trace._TRACER_PROVIDER = None  # noqa: SLF001
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # noqa: SLF001


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in [key for key in os.environ if key.startswith("ROOTCAUSE_")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # no stray .env file is picked up
    _clear_settings_cache_if_available()
    yield
    _clear_settings_cache_if_available()


@pytest.fixture(autouse=True)
def reset_logging_state() -> Iterator[None]:
    yield
    try:
        import structlog
    except ImportError:
        return
    structlog.reset_defaults()


@pytest.fixture(autouse=True)
def reset_telemetry_state() -> Iterator[None]:
    yield
    try:
        from rootcause_core.telemetry import shutdown_telemetry
    except ImportError:
        pass
    else:
        shutdown_telemetry()
    _reset_otel_globals()


@pytest.fixture
def database_url() -> str:
    return DB_URL


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", database_url)
