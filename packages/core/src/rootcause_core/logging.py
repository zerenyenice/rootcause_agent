"""Structured logging bootstrap built on ``structlog`` with stdlib integration.

Both structlog-native and plain ``logging`` records flow through the same
processor chain and renderer, so third-party libraries produce the same JSON
(or console) lines as application code. When an OpenTelemetry span is active,
``trace_id``/``span_id`` are attached so log lines can be joined to traces.
"""

import logging
import re
import sys
from collections.abc import Callable
from typing import TextIO

import structlog
from opentelemetry import trace
from structlog.typing import EventDict, Processor, WrappedLogger

from rootcause_core.settings import LogFormat, Settings

TRACE_ID_FIELD = "trace_id"
SPAN_ID_FIELD = "span_id"


class _StdoutHandler(logging.StreamHandler[TextIO]):
    """Marker subclass so reconfiguration replaces only the handler we own.

    Logging must never take the process down: a write to a closed stream (for
    example a replaced test stdout) is dropped instead of raising.
    """

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self) -> TextIO:
        """Resolve ``sys.stdout`` per write so a swapped or closed stream is never held."""
        return sys.stdout

    @stream.setter
    def stream(self, value: TextIO) -> None:
        """Ignore assignments; the stream is always the live ``sys.stdout``."""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        """Drop closed/broken-stream errors; report anything else without raising.

        The notice carries only the exception type, never record contents.
        """
        error = sys.exc_info()[1]
        if isinstance(error, ValueError | OSError):
            return
        stderr = sys.__stderr__
        if stderr is None:
            return
        name = type(error).__name__ if error is not None else "UnknownError"
        try:
            stderr.write(f"rootcause logging error: {name}\n")
        except (ValueError, OSError):
            return


def add_trace_context(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Attach the current span's ids as zero-padded hex when a span is active."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return event_dict
    return {
        **event_dict,
        TRACE_ID_FIELD: format(context.trace_id, "032x"),
        SPAN_ID_FIELD: format(context.span_id, "016x"),
    }


_URL_CREDENTIALS = re.compile(r"(\w+://[^:/@\s]+:)[^@\s]+@")
REDACTED = "***"


def make_text_redactor(secrets: tuple[str, ...] = ()) -> Callable[[str], str]:
    """Build a function masking URL passwords and verbatim ``secrets`` in a string."""
    known = tuple(secret for secret in secrets if secret)

    def redact_text(text: str) -> str:
        cleaned = _URL_CREDENTIALS.sub(rf"\1{REDACTED}@", text)
        for secret in known:
            cleaned = cleaned.replace(secret, REDACTED)
        return cleaned

    return redact_text


def make_redactor(secrets: tuple[str, ...] = ()) -> Processor:
    """Build a processor masking URL passwords and verbatim ``secrets`` in str values."""
    redact_text = make_text_redactor(secrets)

    def redact(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
        return {
            key: redact_text(value) if isinstance(value, str) else value
            for key, value in event_dict.items()
        }

    return redact


def _shared_processors(redactor: Processor) -> tuple[Processor, ...]:
    return (
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        add_trace_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redactor,
    )


def _renderer(log_format: LogFormat) -> Processor:
    if log_format is LogFormat.JSON:
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer()


def _install_stdout_handler(formatter: logging.Formatter, level: str) -> None:
    """Replace any handler we installed earlier with a fresh one on current stdout."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _StdoutHandler)]:
        root.removeHandler(handler)
        handler.close()
    handler = _StdoutHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib root logger from ``settings``.

    Safe to call more than once; each call rebuilds the stdout handler so the
    stream is resolved at call time rather than cached at import.
    """
    redactor = make_redactor((settings.database_url.get_secret_value(),))
    shared = _shared_processors(redactor)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _renderer(settings.log_format),
        ],
        # ExtraAdder copies stdlib ``extra={...}`` fields into the event dict so
        # third-party records render them like structlog key-value pairs.
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *shared],
    )
    _install_stdout_handler(formatter, settings.log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the stdlib logger ``name``."""
    return structlog.stdlib.get_logger(name)
