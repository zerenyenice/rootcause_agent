"""Langfuse client (tracing + prompt management); the only module importing ``langfuse``.

Lifecycle:

* One :class:`~langfuse.Langfuse` client is cached per :class:`Settings`
  instance. The SDK itself keeps one set of resources per public key, so two
  ``Settings`` with the same key share exporters and background threads.
* Tracing runs on a dedicated OpenTelemetry ``TracerProvider`` owned by this
  module. Langfuse would otherwise register a global provider, which would
  collide with :mod:`rootcause_core.telemetry` (the global provider is set-once).
* :func:`flush_langfuse` pushes buffered spans; ``close_clients()`` flushes and
  shuts the clients down at process exit (API lifespan). Rebuilding a client
  for the same public key after shutdown is not supported by the SDK.
* A client for prompt management alone (``prompt_source=langfuse`` while
  ``langfuse_enabled`` is false) is built with tracing disabled.
"""

import threading
from dataclasses import dataclass
from typing import Final

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import Langfuse, LangfuseOtelSpanAttributes
from langfuse.langchain import CallbackHandler
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

from rootcause_core.errors import ConfigurationError
from rootcause_core.observability.masking import build_mask
from rootcause_core.settings import ENV_PREFIX, LANGFUSE_CREDENTIAL_FIELDS, Settings, get_settings

TEXT_PROMPT_TYPE: Final = "text"

_clients: dict[Settings, Langfuse] = {}
_lock = threading.Lock()


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


def _masked_secrets(settings: Settings, credentials: _Credentials) -> tuple[str, ...]:
    return (settings.database_url.get_secret_value(), credentials.secret_key)


def _build(settings: Settings) -> Langfuse:
    credentials = _credentials(settings)
    tracing = settings.langfuse_enabled
    return Langfuse(
        public_key=credentials.public_key,
        secret_key=credentials.secret_key,
        base_url=credentials.base_url,
        environment=settings.environment.value,
        release=settings.langfuse_release,
        sample_rate=settings.langfuse_sample_rate,
        mask=build_mask(_masked_secrets(settings, credentials)),
        tracing_enabled=tracing,
        tracer_provider=_tracer_provider(settings) if tracing else None,
    )


def _client_for(settings: Settings) -> Langfuse:
    with _lock:
        client = _clients.get(settings)
        if client is None:
            client = _build(settings)
            _clients[settings] = client
        return client


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
    return CallbackHandler(public_key=_credentials(resolved).public_key)


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
    """Flush and shut down every cached client, then forget them (process shutdown)."""
    with _lock:
        clients = list(_clients.values())
        _clients.clear()
    for client in clients:
        client.shutdown()


def reset_langfuse_cache() -> None:
    """Forget cached clients without shutting them down (tests only)."""
    with _lock:
        _clients.clear()
