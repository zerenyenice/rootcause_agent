"""Shared fixtures: isolated ROOTCAUSE_* environment and an app factory."""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://u:p@127.0.0.1:1/db"


def _clear_settings_cache() -> None:
    from rootcause_core.settings import clear_settings_cache

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
        from rootcause_api.main import create_app

        return create_app()

    return _make


@pytest.fixture
def make_client(make_app: Callable[..., "FastAPI"]) -> Callable[..., "TestClient"]:
    """Like ``make_app`` but returns a TestClient (lazy fastapi import)."""

    def _make(**settings_overrides: str) -> "TestClient":
        from fastapi.testclient import TestClient

        return TestClient(make_app(**settings_overrides))

    return _make
