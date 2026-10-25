"""T009: rootcause_core.clients.langfuse (no network: spans go to an in-memory exporter)."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit

SECRET = "sk-lf-SENTINEL-SECRET"
DB = "postgresql://u:p@localhost/db"


def _disabled() -> Settings:
    return Settings(_env_file=None, database_url=DB)


def test_returns_none_when_disabled_and_never_builds_a_client(langfuse_capture: Any) -> None:
    from rootcause_core.clients.langfuse import langfuse_client

    assert langfuse_client(_disabled()) is None
    assert langfuse_capture.init_kwargs == []


def test_builds_client_from_settings(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> None:
    from langfuse import Langfuse

    from rootcause_core.clients.langfuse import langfuse_client

    settings = make_langfuse_settings(langfuse_release="2026.10.1", langfuse_sample_rate=0.5)

    client = langfuse_client(settings)

    assert isinstance(client, Langfuse)
    [kwargs] = langfuse_capture.init_kwargs
    assert kwargs["public_key"] == settings.langfuse_public_key.get_secret_value()  # type: ignore[union-attr]
    assert kwargs["secret_key"] == SECRET
    assert str(kwargs.get("base_url") or kwargs.get("host")).startswith("http://127.0.0.1:9")
    assert kwargs["release"] == "2026.10.1"
    assert kwargs["sample_rate"] == 0.5
    assert callable(kwargs["mask"])


def test_passes_plain_strings_not_secret_wrappers(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> None:
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings())

    [kwargs] = langfuse_capture.init_kwargs
    assert isinstance(kwargs["public_key"], str)
    assert isinstance(kwargs["secret_key"], str)


def test_client_is_cached_per_settings(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> None:
    from rootcause_core.clients.langfuse import langfuse_client

    settings = make_langfuse_settings()

    assert langfuse_client(settings) is langfuse_client(settings)
    assert len(langfuse_capture.init_kwargs) == 1


def test_reset_clients_forgets_the_cached_client(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> None:
    from rootcause_core.clients import reset_clients
    from rootcause_core.clients.langfuse import langfuse_client

    settings = make_langfuse_settings()
    first = langfuse_client(settings)

    reset_clients()

    assert langfuse_client(settings) is not first


def test_defaults_to_process_settings(langfuse_capture: Any, langfuse_env: Settings) -> None:
    from rootcause_core.clients.langfuse import langfuse_client

    assert langfuse_client() is not None
    [kwargs] = langfuse_capture.init_kwargs
    assert kwargs["public_key"] == langfuse_env.langfuse_public_key.get_secret_value()  # type: ignore[union-attr]


def test_flush_langfuse_flushes_the_client(
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langfuse import Langfuse

    from rootcause_core.clients.langfuse import flush_langfuse, langfuse_client

    settings = make_langfuse_settings()
    langfuse_client(settings)
    calls: list[str] = []
    monkeypatch.setattr(Langfuse, "flush", lambda self: calls.append("flush"))

    flush_langfuse()

    assert calls == ["flush"]


def test_flush_langfuse_is_a_noop_without_a_client(langfuse_capture: Any) -> None:
    from rootcause_core.clients.langfuse import flush_langfuse

    flush_langfuse()

    assert langfuse_capture.init_kwargs == []


def test_close_clients_flushes_langfuse(
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langfuse import Langfuse

    from rootcause_core.clients import close_clients
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings())
    calls: list[str] = []
    monkeypatch.setattr(Langfuse, "flush", lambda self: calls.append("flush"))
    monkeypatch.setattr(Langfuse, "shutdown", lambda self: calls.append("shutdown"))

    asyncio.run(close_clients())

    assert calls, "close_clients() must flush (or shut down) the Langfuse client"


def test_close_clients_without_langfuse_does_not_build_one(langfuse_capture: Any) -> None:
    from rootcause_core.clients import close_clients

    asyncio.run(close_clients())

    assert langfuse_capture.init_kwargs == []


def test_a_span_recorded_through_the_client_reaches_the_exporter(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> None:
    from rootcause_core.clients.langfuse import flush_langfuse, langfuse_client

    client = langfuse_client(make_langfuse_settings())
    assert client is not None
    with client.start_as_current_observation(name="probe"):
        pass
    flush_langfuse()

    names = [span.name for span in langfuse_capture.exporter.get_finished_spans()]
    assert "probe" in names
