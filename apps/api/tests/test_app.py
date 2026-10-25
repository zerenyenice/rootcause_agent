"""create_app builds the FastAPI application."""

from collections.abc import Callable
from typing import Any

import pytest


@pytest.mark.unit
def test_create_app_returns_fastapi_instance_with_title(make_app: Callable[..., Any]) -> None:
    from fastapi import FastAPI

    app = make_app()

    assert isinstance(app, FastAPI)
    assert app.title == "RootCause API"


@pytest.mark.unit
def test_openapi_document_lists_health_and_version(make_client: Callable[..., Any]) -> None:
    client = make_client()

    response = client.get("/openapi.json")

    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/version" in paths


@pytest.mark.unit
def test_module_level_app_is_a_fastapi_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/db")

    from rootcause_api.main import app

    assert isinstance(app, FastAPI)
