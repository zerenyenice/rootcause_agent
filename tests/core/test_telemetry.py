"""Tests for rootcause.telemetry."""

import pytest

pytestmark = pytest.mark.unit


def _settings(monkeypatch: pytest.MonkeyPatch, database_url: str, **env: str):
    from rootcause.settings import get_settings

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

    from rootcause.telemetry import configure_telemetry, get_tracer

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
    from rootcause.telemetry import (
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
    from rootcause.telemetry import configure_telemetry

    provider = configure_telemetry(
        _settings(monkeypatch, database_url, otel_exporter="console", service_name="analyzer")
    )

    assert provider.resource.attributes["service.name"] == "analyzer"


def test_configure_telemetry_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from opentelemetry import trace

    from rootcause.telemetry import configure_telemetry

    settings = _settings(monkeypatch, database_url, otel_exporter="console")

    first = configure_telemetry(settings)
    second = configure_telemetry(settings)

    assert first is second
    assert trace.get_tracer_provider() is first


def test_shutdown_telemetry_flushes_without_error(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from rootcause.telemetry import (
        configure_telemetry,
        get_tracer,
        shutdown_telemetry,
    )

    configure_telemetry(_settings(monkeypatch, database_url, otel_exporter="console"))
    with get_tracer("t").start_as_current_span("s"):
        pass

    shutdown_telemetry()


def test_shutdown_telemetry_without_configuration_is_safe() -> None:
    from rootcause.telemetry import shutdown_telemetry

    shutdown_telemetry()


def test_none_exporter_installs_provider_that_exports_nothing(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], database_url: str
) -> None:
    from opentelemetry.sdk.trace import TracerProvider

    from rootcause.telemetry import (
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


def test_configure_after_shutdown_raises_because_global_provider_cannot_be_replaced(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    from rootcause.errors import ConfigurationError
    from rootcause.telemetry import configure_telemetry, shutdown_telemetry

    settings = _settings(monkeypatch, database_url, otel_exporter="console")
    configure_telemetry(settings)
    shutdown_telemetry()

    with pytest.raises(ConfigurationError):
        configure_telemetry(settings)


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://c:4318", "http://c:4318/v1/traces"),
        ("http://c:4318/", "http://c:4318/v1/traces"),
        ("http://c:4318/v1/traces", "http://c:4318/v1/traces"),
        ("http://c:4318/v1/traces/", "http://c:4318/v1/traces"),
    ],
)
def test_traces_endpoint_resolves_otlp_traces_url(base: str, expected: str) -> None:
    from rootcause.telemetry import traces_endpoint

    assert traces_endpoint(base) == expected


def test_otlp_exporter_registers_batch_processor_and_exports_nothing_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], database_url: str
) -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import SpanExportResult

    from rootcause.telemetry import (
        configure_telemetry,
        get_tracer,
        shutdown_telemetry,
    )

    monkeypatch.setattr(OTLPSpanExporter, "export", lambda self, spans: SpanExportResult.SUCCESS)
    provider = configure_telemetry(
        _settings(
            monkeypatch,
            database_url,
            otel_exporter="otlp",
            otel_endpoint="http://127.0.0.1:1",
        )
    )
    processors = provider._active_span_processor._span_processors  # noqa: SLF001
    with get_tracer("t").start_as_current_span("otlp-span"):
        pass
    shutdown_telemetry()

    assert "BatchSpanProcessor" in [type(p).__name__ for p in processors]
    assert "otlp-span" not in capfd.readouterr().out
