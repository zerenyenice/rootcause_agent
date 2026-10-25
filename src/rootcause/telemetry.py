"""OpenTelemetry tracing bootstrap.

:func:`configure_telemetry` installs one SDK ``TracerProvider`` per process.
Even with the ``none`` exporter a real provider is installed so spans carry
valid ids that :mod:`rootcause.logging` can stamp onto log lines.

OpenTelemetry's global tracer provider is **set-once**: after the first
successful registration it cannot be replaced for the lifetime of the process.
:func:`configure_telemetry` is therefore idempotent while its provider is live
and refuses (with :class:`~rootcause.errors.ConfigurationError`) to
configure again once the registration cannot be verified, for example after
:func:`shutdown_telemetry` or when other code registered a provider first.
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

from rootcause.errors import ConfigurationError
from rootcause.settings import OtelExporter, Settings

OTLP_TRACES_PATH = "/v1/traces"
SERVICE_NAME_ATTRIBUTE = "service.name"
DEPLOYMENT_ENVIRONMENT_ATTRIBUTE = "deployment.environment.name"

# The only module-level mutable state in this package: the provider installed by
# configure_telemetry(), kept so repeated calls are idempotent and shutdown can
# flush it. OpenTelemetry's own global cannot be unset, hence the private holder.
_provider: TracerProvider | None = None


def traces_endpoint(base: str) -> str:
    """Resolve the OTLP/HTTP traces URL from a base endpoint, per the OTLP spec.

    Trailing slashes are dropped and ``/v1/traces`` appended unless the path
    already ends with it, so ``http://c:4318``, ``http://c:4318/`` and
    ``http://c:4318/v1/traces/`` all resolve to ``http://c:4318/v1/traces``.
    """
    stripped = base.rstrip("/")
    if stripped.endswith(OTLP_TRACES_PATH):
        return stripped
    return f"{stripped}{OTLP_TRACES_PATH}"


def _build_span_processor(settings: Settings) -> SpanProcessor | None:
    if settings.otel_exporter is OtelExporter.CONSOLE:
        # ``out`` is passed explicitly so the stream is bound at configure time.
        return SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stdout))
    if settings.otel_exporter is OtelExporter.OTLP:
        endpoint = traces_endpoint(str(settings.otel_endpoint)) if settings.otel_endpoint else None
        return BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    return None


def _build_provider(settings: Settings) -> TracerProvider:
    resource = Resource.create(
        {
            SERVICE_NAME_ATTRIBUTE: settings.service_name,
            DEPLOYMENT_ENVIRONMENT_ATTRIBUTE: str(settings.environment),
        }
    )
    provider = TracerProvider(resource=resource)
    processor = _build_span_processor(settings)
    if processor is not None:
        provider.add_span_processor(processor)
    return provider


def configure_telemetry(settings: Settings) -> TracerProvider:
    """Install and return the process-wide tracer provider.

    Idempotent while the provider installed by this function is live: repeated
    calls return the same object. Because OpenTelemetry's global provider is
    set-once, a call made after :func:`shutdown_telemetry`, or after another
    component registered a provider, cannot take effect.

    Raises:
        ConfigurationError: when the global provider slot is already taken and
            the new provider could not be registered; restart the process to
            reconfigure telemetry.
    """
    global _provider
    if _provider is not None:
        return _provider
    provider = _build_provider(settings)
    trace.set_tracer_provider(provider)
    if trace.get_tracer_provider() is not provider:
        provider.shutdown()
        raise ConfigurationError(
            "telemetry provider already registered in this process; "
            "restart the process to reconfigure"
        )
    _provider = provider
    return provider


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer from the globally registered provider."""
    return trace.get_tracer(name)


def shutdown_telemetry() -> None:
    """Flush and shut down the configured provider; a no-op when none exists.

    Clears the private holder, but the OpenTelemetry global stays bound to the
    shut-down provider (set-once), so a later :func:`configure_telemetry`
    raises :class:`~rootcause.errors.ConfigurationError`.
    """
    global _provider
    provider, _provider = _provider, None
    if provider is None:
        return
    provider.force_flush()
    provider.shutdown()
