"""Shared fixtures: isolate env vars, settings cache, structlog and OTel globals."""

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

DB_URL = "postgresql://user:s3cr3t-pw@localhost:5432/rootcause"


def _clear_settings_cache_if_available() -> None:
    try:
        from rootcause_core.settings import clear_settings_cache
    except ImportError:
        return
    clear_settings_cache()


def _reset_otel_globals() -> None:
    """Allow a fresh global TracerProvider to be installed in the next test."""
    try:
        from opentelemetry import trace
    except ImportError:
        return
    trace._TRACER_PROVIDER = None  # noqa: SLF001
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # noqa: SLF001


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in [
        key
        for key in os.environ
        if key.startswith("ROOTCAUSE_") and not key.startswith("ROOTCAUSE_TEST_")
    ]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # no stray .env file is picked up
    _clear_settings_cache_if_available()
    yield
    _clear_settings_cache_if_available()


@pytest.fixture(autouse=True)
def reset_logging_state() -> Iterator[None]:
    yield
    try:
        import structlog
    except ImportError:
        return
    structlog.reset_defaults()


@pytest.fixture(autouse=True)
def reset_telemetry_state() -> Iterator[None]:
    yield
    try:
        from rootcause_core.telemetry import shutdown_telemetry
    except ImportError:
        pass
    else:
        shutdown_telemetry()
    _reset_otel_globals()


@pytest.fixture
def database_url() -> str:
    return DB_URL


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", database_url)


@dataclass
class LangfuseCapture:
    """Spans exported by every Langfuse client built while the fixture is active."""

    exporter: Any
    init_kwargs: list[dict[str, Any]] = field(default_factory=list)


def _reset_langfuse_state() -> None:
    try:
        from langfuse._client.resource_manager import LangfuseResourceManager
    except ImportError:
        return
    LangfuseResourceManager.reset()


@pytest.fixture
def langfuse_capture(monkeypatch: pytest.MonkeyPatch) -> Iterator[LangfuseCapture]:
    """Route every Langfuse export to an in-memory span exporter (no network).

    ``Langfuse.__init__`` is wrapped, not replaced: the real client is built and the
    keyword arguments the code under test passed are recorded. The OTLP exporter's
    ``export`` is replaced, so spans reach memory after any wrapper the code adds.
    """
    try:
        import langfuse
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import SpanExportResult
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    except ImportError:
        pytest.fail("the 'langfuse' package is not installed (add it as a core dependency)")
    capture = LangfuseCapture(exporter=InMemorySpanExporter())
    original = langfuse.Langfuse.__init__

    def init(self: Any, **kwargs: Any) -> None:
        capture.init_kwargs.append(dict(kwargs))
        original(self, **kwargs)

    # Whatever exporter chain the code under test builds (including a redacting
    # wrapper around the OTLP exporter), the final network hop lands in memory.
    def export(_self: Any, spans: Any) -> Any:
        capture.exporter.export(spans)
        return SpanExportResult.SUCCESS

    monkeypatch.setattr(langfuse.Langfuse, "__init__", init)
    monkeypatch.setattr(OTLPSpanExporter, "export", export)
    yield capture
    from rootcause_core.clients import reset_clients

    reset_clients()
    _reset_langfuse_state()
    _reset_otel_globals()


@pytest.fixture
def make_langfuse_settings() -> Callable[..., Any]:
    """Build enabled Langfuse settings with a unique public key (clients are keyed by it)."""
    from rootcause_core.settings import Settings

    def make(**overrides: Any) -> Any:
        values: dict[str, Any] = {
            "database_url": DB_URL,
            "environment": "test",
            "langfuse_enabled": True,
            "langfuse_host": "http://127.0.0.1:9",
            "langfuse_public_key": f"pk-lf-{uuid.uuid4().hex}",
            "langfuse_secret_key": "sk-lf-SENTINEL-SECRET",
        }
        return Settings(_env_file=None, **{**values, **overrides})

    return make


@pytest.fixture
def langfuse_env(
    monkeypatch: pytest.MonkeyPatch, make_langfuse_settings: Callable[..., Any]
) -> Any:
    """Enabled Langfuse settings exported as ``ROOTCAUSE_*`` variables; returns the Settings."""
    settings = make_langfuse_settings()
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", DB_URL)
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "test")
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_HOST", str(settings.langfuse_host))
    monkeypatch.setenv(
        "ROOTCAUSE_LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key.get_secret_value()
    )
    monkeypatch.setenv(
        "ROOTCAUSE_LANGFUSE_SECRET_KEY", settings.langfuse_secret_key.get_secret_value()
    )
    return settings
