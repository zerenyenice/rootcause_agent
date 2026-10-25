"""OpenTelemetry tracing bootstrap.

:func:`configure_telemetry` installs one SDK ``TracerProvider`` per process.
Even with the ``none`` exporter a real provider is installed so spans carry
valid ids that :mod:`rootcause_core.logging` can stamp onto log lines.
"""

import sys

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from rootcause_core.settings import OtelExporter, Settings

OTLP_TRACES_PATH = "/v1/traces"

# The only module-level mutable state in this package: the provider installed by
# configure_telemetry(), kept so repeated calls are idempotent and shutdown can
# flush it. OpenTelemetry's own global cannot be unset, hence the private holder.
_provider: TracerProvider | None = None


def _traces_endpoint(base_url: str) -> str:
    """Resolve the OTLP/HTTP traces URL from a base endpoint, per the OTLP spec."""
    if base_url.endswith(OTLP_TRACES_PATH):
        return base_url
    return f"{base_url.rstrip('/')}{OTLP_TRACES_PATH}"


def _build_span_processor(settings: Settings) -> SpanProcessor | None:
    if settings.otel_exporter is OtelExporter.CONSOLE:
        # ``out`` is passed explicitly so the stream is bound at configure time.
        return SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stdout))
    if settings.otel_exporter is OtelExporter.OTLP:
        endpoint = _traces_endpoint(settings.otel_endpoint) if settings.otel_endpoint else None
        return BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    return None


def configure_telemetry(settings: Settings) -> TracerProvider:
    """Install and return the process-wide tracer provider (idempotent)."""
    global _provider
    if _provider is not None:
        return _provider
    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "deployment.environment": str(settings.environment),
        }
    )
    provider = TracerProvider(resource=resource)
    processor = _build_span_processor(settings)
    if processor is not None:
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _provider = provider
    return provider


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer from the globally registered provider."""
    return trace.get_tracer(name)


def shutdown_telemetry() -> None:
    """Flush and shut down the configured provider; a no-op when none exists.

    Clears the idempotence state so a later :func:`configure_telemetry`
    installs a fresh provider.
    """
    global _provider
    provider, _provider = _provider, None
    if provider is None:
        return
    provider.force_flush()
    provider.shutdown()
