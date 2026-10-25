"""Shared offline fixtures: a scripted chat model and a recording SQL executor."""

import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable


class ScriptedChatModel(FakeMessagesListChatModel):
    """Replays a fixed list of AIMessages and tolerates ``bind_tools``."""

    def bind_tools(  # type: ignore[override]
        self, tools: Sequence[Any], **kwargs: Any
    ) -> Runnable[Any, BaseMessage]:
        return self


def _tool_call_message(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _scripted_model(*messages: AIMessage) -> ScriptedChatModel:
    return ScriptedChatModel(responses=list(messages))


@dataclass
class FakeExecutor:
    """Records calls; never touches a database."""

    estimated_rows: int = 10
    estimated_cost: float = 1.5
    explain_calls: list[str] = field(default_factory=list)
    run_calls: list[tuple[str, str]] = field(default_factory=list)
    error: Exception | None = None

    def explain(self, sql: str) -> Any:
        from rootcause.agents.tools.sql import QueryEstimate

        self.explain_calls.append(sql)
        if self.error is not None:
            raise self.error
        return QueryEstimate(estimated_rows=self.estimated_rows, estimated_cost=self.estimated_cost)

    def run(self, sql: str, purpose: str) -> Any:
        from rootcause.agents.tools.sql import QueryResult

        self.run_calls.append((sql, purpose))
        if self.error is not None:
            raise self.error
        return QueryResult(columns=("n",), rows=((1,),), row_count=1, truncated=False, sql=sql)


@pytest.fixture
def executor() -> FakeExecutor:
    return FakeExecutor()


@pytest.fixture
def make_executor() -> Callable[..., FakeExecutor]:
    return FakeExecutor


@pytest.fixture
def scripted_model() -> Callable[..., ScriptedChatModel]:
    return _scripted_model


@pytest.fixture
def tool_call_message() -> Callable[[str, dict[str, Any], str], AIMessage]:
    return _tool_call_message


DB_URL = "postgresql://user:s3cr3t-pw@localhost:5432/rootcause"


@dataclass
class LangfuseCapture:
    """Spans exported by every Langfuse client built while the fixture is active."""

    exporter: Any
    init_kwargs: list[dict[str, Any]] = field(default_factory=list)


def _reset_global_state() -> None:
    from opentelemetry import trace

    try:
        from langfuse._client.resource_manager import LangfuseResourceManager
    except ImportError:
        pass
    else:
        LangfuseResourceManager.reset()
    trace._TRACER_PROVIDER = None  # noqa: SLF001
    trace._TRACER_PROVIDER_SET_ONCE._done = False  # noqa: SLF001


@pytest.fixture(autouse=True)
def _clean_rootcause_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import os

    from rootcause.settings import clear_settings_cache

    for name in [key for key in os.environ if key.startswith("ROOTCAUSE_")]:
        monkeypatch.delenv(name)
    clear_settings_cache()
    yield
    clear_settings_cache()


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
    from rootcause.clients import reset_clients

    reset_clients()
    _reset_global_state()


@pytest.fixture
def make_langfuse_settings() -> Callable[..., Any]:
    """Build enabled Langfuse settings with a unique public key (clients are keyed by it)."""
    from rootcause.settings import Settings

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
