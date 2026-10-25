"""Span exporter wrapper that scrubs every exported string before it leaves the process.

Langfuse's ``mask`` only sees observation input, output and metadata. Other SDK
paths write strings straight onto the OpenTelemetry span (status descriptions
from errors, exception events, propagated trace attributes, the resource). This
wrapper sits directly in front of the network exporter and rewrites span
attributes, the status description, event attributes and resource attributes
through the same scrubber (credential redaction plus the optional PII pass).
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status

Scrub = Callable[[str], str]


def _scrub_value(value: Any, scrub: Scrub) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, Mapping):
        return {key: _scrub_value(item, scrub) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return tuple(_scrub_value(item, scrub) for item in value)
    return value


def _scrub_attributes(attributes: Mapping[str, Any] | None, scrub: Scrub) -> dict[str, Any]:
    return {key: _scrub_value(value, scrub) for key, value in (attributes or {}).items()}


def _scrub_status(status: Status, scrub: Scrub) -> Status:
    if status.description is None:
        return status
    return Status(status.status_code, scrub(status.description))


def _scrub_resource(resource: Resource, scrub: Scrub) -> Resource:
    return Resource(_scrub_attributes(resource.attributes, scrub), resource.schema_url)


def scrub_span(span: ReadableSpan, scrub: Scrub) -> ReadableSpan:
    """Return a copy of ``span`` with every string attribute, status and event scrubbed."""
    events = tuple(
        Event(event.name, _scrub_attributes(event.attributes, scrub), event.timestamp)
        for event in span.events
    )
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=_scrub_resource(span.resource, scrub),
        attributes=_scrub_attributes(span.attributes, scrub),
        events=events,
        links=span.links,
        kind=span.kind,
        status=_scrub_status(span.status, scrub),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class ScrubbingSpanExporter(SpanExporter):
    """Delegate to ``exporter`` after scrubbing each span (see module docstring)."""

    def __init__(self, exporter: SpanExporter, scrub: Scrub) -> None:
        self._exporter = exporter
        self._scrub = scrub

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Scrub ``spans`` and hand them to the wrapped exporter."""
        return self._exporter.export([scrub_span(span, self._scrub) for span in spans])

    def shutdown(self) -> None:
        """Shut the wrapped exporter down."""
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Flush the wrapped exporter."""
        return self._exporter.force_flush(timeout_millis)
