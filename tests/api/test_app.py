"""create_app builds the FastAPI application."""

import json
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
def test_main_module_has_no_module_level_app() -> None:
    import rootcause.api.main as main

    assert not hasattr(main, "app")
    assert callable(main.create_app)


@pytest.mark.unit
def test_create_app_twice_warns_about_telemetry_without_raising(
    make_app: Callable[..., Any], capsys: pytest.CaptureFixture[str]
) -> None:
    from fastapi import FastAPI

    from rootcause.telemetry import shutdown_telemetry

    first = make_app()
    shutdown_telemetry()
    capsys.readouterr()
    second = make_app()

    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    warnings = [
        event
        for event in events
        if event.get("level") == "warning" and "telemetry" in str(event.get("event"))
    ]
    assert isinstance(first, FastAPI)
    assert isinstance(second, FastAPI)
    assert first is not second
    assert warnings
