"""Langfuse client (tracing + prompt management); the only module importing ``langfuse``.

Lifecycle:

* One :class:`~langfuse.Langfuse` client is cached per :class:`Settings`
  instance. The SDK itself keeps one set of resources per public key, so two
  ``Settings`` with the same key share exporters and background threads.
* Tracing runs on a dedicated OpenTelemetry ``TracerProvider`` owned by this
  module. Langfuse would otherwise register a global provider, which would
  collide with :mod:`rootcause_core.telemetry` (the global provider is set-once).
* :func:`flush_langfuse` pushes buffered spans; ``close_clients()`` shuts every
  client down at process exit (API lifespan), each one independently. Rebuilding
  a client for the same public key after shutdown is not supported by the SDK;
  doing so logs a warning.
* A client for prompt management alone (``prompt_source=langfuse`` while
  ``langfuse_enabled`` is false) is built with tracing disabled.

Data protection (PROJECT.md section 7):

* ``mask`` (:mod:`rootcause_core.observability.masking`) denies tool outputs by
  default and scrubs credentials and PII from observation data.
* The OTLP exporter is wrapped in a
  :class:`~rootcause_core.observability.export.ScrubbingSpanExporter`, which
  scrubs span attributes, status descriptions, events and the resource — the
  paths ``mask`` never sees.
* The LangChain handler is hardened (:class:`_HandlerGuard`): every root run
  starts from an empty OpenTelemetry context (a fresh trace, never a child of an
  unrelated span such as FastAPI's) and errors are recorded by exception type
  only, because exception messages routinely carry row values.
"""

import base64
import dataclasses
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any, Final
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage
from langfuse import Langfuse, LangfuseOtelSpanAttributes
from langfuse._client.propagation import _detach_context_token_safely
from langfuse.langchain import CallbackHandler
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

from rootcause_core.errors import ConfigurationError
from rootcause_core.logging import get_logger
from rootcause_core.observability.export import ScrubbingSpanExporter
from rootcause_core.observability.masking import build_mask, build_scrubber
from rootcause_core.settings import (
    ENV_PREFIX,
    LANGFUSE_CREDENTIAL_FIELDS,
    Settings,
    get_settings,
    secret_values,
)

TEXT_PROMPT_TYPE: Final = "text"
OTLP_TRACES_PATH: Final = "api/public/otel/v1/traces"
EXPORT_TIMEOUT_S: Final = 5
INGESTION_VERSION: Final = "4"
ERROR_LEVEL: Final = "ERROR"
TOOL_ERROR_MARKER: Final = "<redacted tool error: {name}>"
ROOT_SPAN_METADATA: Final = ("tenant_id", "connection_id")
ROOT_SPAN_ATTRIBUTE_PREFIX: Final = "rootcause."

_clients: dict[Settings, Langfuse] = {}
_shut_down_keys: set[str] = set()
_lock = threading.Lock()
_logger = get_logger(__name__)


@dataclass(frozen=True)
class LangfusePrompt:
    """A text prompt version fetched from Langfuse."""

    name: str
    version: int
    text: str


@dataclass(frozen=True)
class _Credentials:
    base_url: str
    public_key: str
    secret_key: str


def _credentials(settings: Settings) -> _Credentials:
    """Return host and keys, or raise a value-free error naming every missing variable.

    Raises:
        ConfigurationError: the host or either key is unset.
    """
    missing = tuple(
        f"{ENV_PREFIX}{name.upper()}"
        for name in LANGFUSE_CREDENTIAL_FIELDS
        if getattr(settings, name) is None
    )
    host, public_key, secret_key = (
        settings.langfuse_host,
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
    )
    if missing or host is None or public_key is None or secret_key is None:
        raise ConfigurationError(f"Langfuse needs {', '.join(missing)}.", variables=missing)
    return _Credentials(
        base_url=str(host).rstrip("/"),
        public_key=public_key.get_secret_value(),
        secret_key=secret_key.get_secret_value(),
    )


def _tracer_provider(settings: Settings) -> TracerProvider:
    """A private provider so Langfuse never registers (or reuses) the global one."""
    attributes = {LangfuseOtelSpanAttributes.ENVIRONMENT: settings.environment.value}
    if settings.langfuse_release is not None:
        attributes[LangfuseOtelSpanAttributes.RELEASE] = settings.langfuse_release
    rate = settings.langfuse_sample_rate
    return TracerProvider(
        resource=Resource.create(attributes),
        sampler=TraceIdRatioBased(rate) if rate < 1.0 else None,
    )


def _span_exporter(settings: Settings, credentials: _Credentials) -> SpanExporter:
    """The SDK's OTLP exporter (same endpoint and headers) behind the scrubbing wrapper."""
    token = base64.b64encode(f"{credentials.public_key}:{credentials.secret_key}".encode()).decode(
        "ascii"
    )
    headers = {
        "Authorization": f"Basic {token}",
        "x-langfuse-sdk-name": "python",
        "x-langfuse-sdk-version": version("langfuse"),
        "x-langfuse-public-key": credentials.public_key,
        "x-langfuse-ingestion-version": INGESTION_VERSION,
    }
    exporter = OTLPSpanExporter(
        endpoint=f"{credentials.base_url}/{OTLP_TRACES_PATH}",
        headers=headers,
        timeout=EXPORT_TIMEOUT_S,
        compression=Compression.Gzip,
    )
    scrub = build_scrubber(secret_values(settings), mask_pii=settings.langfuse_mask_pii)
    return ScrubbingSpanExporter(exporter, scrub)


def _build(settings: Settings) -> Langfuse:
    credentials = _credentials(settings)
    tracing = settings.langfuse_enabled
    if credentials.public_key in _shut_down_keys:
        _logger.warning("langfuse_client_rebuilt_after_shutdown")
    return Langfuse(
        public_key=credentials.public_key,
        secret_key=credentials.secret_key,
        base_url=credentials.base_url,
        environment=settings.environment.value,
        release=settings.langfuse_release,
        sample_rate=settings.langfuse_sample_rate,
        mask=build_mask(secret_values(settings), mask_pii=settings.langfuse_mask_pii),
        tracing_enabled=tracing,
        tracer_provider=_tracer_provider(settings) if tracing else None,
        span_exporter=_span_exporter(settings, credentials) if tracing else None,
    )


def _client_for(settings: Settings) -> Langfuse:
    with _lock:
        client = _clients.get(settings)
        if client is None:
            client = _build(settings)
            _clients[settings] = client
        return client


def _named_tool_messages(output: Any, name: str | None) -> Any:
    """Give unnamed tool messages (directly or inside a ``Command``) the tool's name.

    The trace mask decides by tool name; middleware tools such as ``write_todos``
    return messages without one.
    """
    if not name:
        return output
    if isinstance(output, ToolMessage):
        return output if output.name else output.model_copy(update={"name": name})
    # A LangGraph ``Command`` (a dataclass with an ``update`` mapping); core does not
    # depend on langgraph, so it is matched by shape.
    update = getattr(output, "update", None)
    if not dataclasses.is_dataclass(output) or isinstance(output, type):
        return output
    if not isinstance(update, Mapping) or not isinstance(update.get("messages"), list):
        return output
    messages = [_named_tool_messages(message, name) for message in update["messages"]]
    return dataclasses.replace(output, update={**update, "messages": messages})


def _sanitised_tool_output(output: Any) -> Any:
    """Error tool messages become a marker: their content is the span status message."""
    if isinstance(output, ToolMessage) and output.status == "error":
        marker = TOOL_ERROR_MARKER.format(name=output.name or "unknown")
        return output.model_copy(update={"content": marker, "artifact": None})
    return output


@dataclass
class _HandlerGuard:
    """Run isolation and value-free errors for one Langfuse LangChain handler.

    Installed on a plain SDK :class:`~langfuse.langchain.CallbackHandler`
    instance (the handler type stays the SDK's own) by wrapping its callbacks:

    * a root run (no parent) starts from an empty OpenTelemetry context, so its
      trace is fresh and never a child of whatever span the caller had active;
      the empty context is detached when the root run ends or fails;
    * the root span carries ``rootcause.tenant_id`` / ``rootcause.connection_id``
      from the run metadata as plain attributes;
    * error observations record the exception type only, and error tool
      messages a marker instead of their content;
    * unnamed tool messages get the tool's name so the mask can apply its
      allowlist.

    Relies on two internals of the pinned Langfuse SDK
    (``_get_error_level_and_status_message`` and ``_detach_context_token_safely``).
    """

    handler: CallbackHandler
    tokens: dict[UUID, object] = field(default_factory=dict)

    def install(self) -> CallbackHandler:
        base = self.handler
        wrappers = {
            "on_chain_start": self._chain_start(base.on_chain_start),
            "on_chain_end": self._restoring(base.on_chain_end),
            "on_chain_error": self._restoring(base.on_chain_error),
            "on_tool_end": self._tool_end(base.on_tool_end),
            "_get_error_level_and_status_message": self._error_message(
                base._get_error_level_and_status_message  # noqa: SLF001
            ),
        }
        for name, wrapper in wrappers.items():
            setattr(base, name, wrapper)
        return base

    def _chain_start(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def on_chain_start(
            serialized: Any,
            inputs: Any,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> Any:
            if parent_run_id is not None:
                return original(
                    serialized, inputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs
                )
            self.tokens[run_id] = otel_context.attach(otel_context.Context())
            result = original(serialized, inputs, run_id=run_id, parent_run_id=None, **kwargs)
            _tag_root_span(kwargs.get("metadata"))
            return result

        return on_chain_start

    def _restoring(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def restoring(*args: Any, run_id: UUID, **kwargs: Any) -> Any:
            try:
                return original(*args, run_id=run_id, **kwargs)
            finally:
                token = self.tokens.pop(run_id, None)
                if token is not None:
                    _detach_context_token_safely(token)

        return restoring

    @staticmethod
    def _tool_end(original: Callable[..., Any]) -> Callable[..., Any]:
        def on_tool_end(output: Any, **kwargs: Any) -> Any:
            named = _named_tool_messages(output, kwargs.get("name"))
            return original(_sanitised_tool_output(named), **kwargs)

        return on_tool_end

    @staticmethod
    def _error_message(original: Callable[..., Any]) -> Callable[..., Any]:
        def error_level_and_status_message(error: BaseException) -> tuple[Any, str]:
            level, message = original(error)
            return (level, type(error).__name__) if level == ERROR_LEVEL else (level, message)

        return error_level_and_status_message


def _tag_root_span(metadata: Any) -> None:
    """Copy the opaque tenant and connection ids onto the (current) root span."""
    if not isinstance(metadata, Mapping):
        return
    span = trace.get_current_span()
    for key in ROOT_SPAN_METADATA:
        value = metadata.get(key)
        if isinstance(value, str):
            span.set_attribute(f"{ROOT_SPAN_ATTRIBUTE_PREFIX}{key}", value)


def langfuse_client(settings: Settings | None = None) -> Langfuse | None:
    """Return the cached tracing client, or ``None`` when ``langfuse_enabled`` is false.

    Raises:
        ConfigurationError: Langfuse is enabled but its host or keys are unset.
    """
    resolved = settings if settings is not None else get_settings()
    if not resolved.langfuse_enabled:
        return None
    return _client_for(resolved)


def langfuse_callback_handler(settings: Settings | None = None) -> BaseCallbackHandler | None:
    """Return a fresh LangChain callback handler bound to the client, or ``None`` if disabled."""
    resolved = settings if settings is not None else get_settings()
    if langfuse_client(resolved) is None:
        return None
    handler = CallbackHandler(public_key=_credentials(resolved).public_key)
    return _HandlerGuard(handler).install()


def fetch_prompt(name: str, settings: Settings) -> LangfusePrompt:
    """Fetch the text prompt ``name`` at ``settings.prompt_label`` (SDK-cached).

    Raises:
        ConfigurationError: the Langfuse host or keys are unset.
        TypeError: the stored prompt is not a text prompt.
        Exception: any SDK or network error, unchanged, for the caller to handle.
    """
    prompt = _client_for(settings).get_prompt(
        name,
        label=settings.prompt_label,
        type=TEXT_PROMPT_TYPE,
        cache_ttl_seconds=settings.prompt_cache_ttl_s,
    )
    if not isinstance(prompt.prompt, str):
        raise TypeError("Langfuse prompt is not a text prompt")
    return LangfusePrompt(name=prompt.name, version=prompt.version, text=prompt.prompt)


def push_prompt(name: str, text: str, settings: Settings, *, labels: tuple[str, ...] = ()) -> None:
    """Create a new version of the text prompt ``name`` (labels are opt-in).

    Raises:
        ConfigurationError: the Langfuse host or keys are unset.
    """
    _client_for(settings).create_prompt(
        name=name, prompt=text, labels=list(labels), type=TEXT_PROMPT_TYPE
    )


def flush_langfuse() -> None:
    """Export buffered spans of every cached client; a no-op when none exists."""
    with _lock:
        clients = list(_clients.values())
    for client in clients:
        client.flush()


def shutdown_langfuse() -> None:
    """Flush and shut down every cached client, then forget them (process shutdown).

    Blocking (the SDK flushes over the network): call it from a worker thread in
    async code, as ``close_clients()`` does. Every client is attempted; a failure
    is logged by exception type and does not stop the others.
    """
    with _lock:
        clients = list(_clients.items())
        _clients.clear()
    for settings, client in clients:
        try:
            client.shutdown()
        except Exception as error:  # noqa: BLE001 - one failing client must not skip the rest
            _logger.error("langfuse_shutdown_failed", error_type=type(error).__name__)
        finally:
            if settings.langfuse_public_key is not None:
                _shut_down_keys.add(settings.langfuse_public_key.get_secret_value())


def reset_langfuse_cache() -> None:
    """Forget cached clients without shutting them down (tests only)."""
    with _lock:
        _clients.clear()
        _shut_down_keys.clear()
