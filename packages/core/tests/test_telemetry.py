"""Tests for rootcause_core.telemetry."""

import pytest

pytestmark = pytest.mark.unit


def _settings(monkeypatch: pytest.MonkeyPatch, database_url: str, **env: str):
    from rootcause_core.settings import get_settings

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", database_url)
    for key, value in env.items():
        monkeypatch.setenv(f"ROOTCAUSE_{key.upper()}", value)
    return get_settings()


def test_configure_telemetry_returns_sdk_provider_whose_spans_are_exported(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from rootcause_core.telemetry import configure_telemetry, get_tracer

    provider = configure_telemetry(_settings(monkeypatch, database_url, otel_exporter="console"))
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))

    with get_tracer("t").start_as_current_span("work"):
        pass

    assert isinstance(provider, TracerProvider)
    assert [span.name for span in memory.get_finished_spans()] == ["work"]


def test_console_exporter_writes_span_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], database_url: str
) -> None:
    from rootcause_core.telemetry import (
        configure_telemetry,
        get_tracer,
        shutdown_telemetry,
    )

    configure_telemetry(_settings(monkeypatch, database_url, otel_exporter="console"))

    with get_tracer("t").start_as_current_span("console-span"):
        pass
    shutdown_telemetry()

    assert "console-span" in capfd.readouterr().out


def test_resource_carries_configured_service_name(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from rootcause_core.telemetry import configure_telemetry

    provider = configure_telemetry(
        _settings(monkeypatch, database_url, otel_exporter="console", service_name="analyzer")
    )

    assert provider.resource.attributes["service.name"] == "analyzer"


def test_configure_telemetry_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from opentelemetry import trace

    from rootcause_core.telemetry import configure_telemetry

    settings = _settings(monkeypatch, database_url, otel_exporter="console")

    first = configure_telemetry(settings)
    second = configure_telemetry(settings)

    assert first is second
    assert trace.get_tracer_provider() is first


def test_shutdown_telemetry_flushes_without_error(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from rootcause_core.telemetry import (
        configure_telemetry,
        get_tracer,
        shutdown_telemetry,
    )

    configure_telemetry(_settings(monkeypatch, database_url, otel_exporter="console"))
    with get_tracer("t").start_as_current_span("s"):
        pass

    shutdown_telemetry()


def test_shutdown_telemetry_without_configuration_is_safe() -> None:
    from rootcause_core.telemetry import shutdown_telemetry

    shutdown_telemetry()


def test_none_exporter_installs_provider_that_exports_nothing(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], database_url: str
) -> None:
    from opentelemetry.sdk.trace import TracerProvider

    from rootcause_core.telemetry import (
        configure_telemetry,
        get_tracer,
        shutdown_telemetry,
    )

    provider = configure_telemetry(_settings(monkeypatch, database_url, otel_exporter="none"))
    with get_tracer("t").start_as_current_span("silent-span") as span:
        is_valid = span.get_span_context().is_valid
    shutdown_telemetry()
    captured = capfd.readouterr()

    assert isinstance(provider, TracerProvider)
    assert is_valid  # trace ids still exist so logs can be correlated
    assert "silent-span" not in captured.out + captured.err
