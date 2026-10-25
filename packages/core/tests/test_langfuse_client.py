"""T009: rootcause_core.clients.langfuse (no network: spans go to an in-memory exporter)."""

import asyncio
import contextlib
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


# --- T009 amendment: lifecycle ---


def test_close_clients_runs_langfuse_shutdown_off_the_event_loop_thread(
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from langfuse import Langfuse

    from rootcause_core.clients import close_clients
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings())
    threads: list[threading.Thread] = []
    monkeypatch.setattr(
        Langfuse, "shutdown", lambda self: threads.append(threading.current_thread())
    )

    async def run() -> threading.Thread:
        loop_thread = threading.current_thread()
        await close_clients()
        return loop_thread

    loop_thread = asyncio.run(run())

    assert len(threads) == 1
    assert threads[0] is not loop_thread
    assert threads[0] is not threading.main_thread()


def test_a_slow_langfuse_shutdown_does_not_block_the_event_loop(
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from langfuse import Langfuse

    from rootcause_core.clients import close_clients
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings())
    monkeypatch.setattr(Langfuse, "shutdown", lambda self: time.sleep(0.5))

    async def run() -> int:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.create_task(ticker())
        await close_clients()
        task.cancel()
        return ticks

    assert asyncio.run(run()) >= 5


def test_close_clients_shuts_every_client_down_even_if_one_raises(
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langfuse import Langfuse

    from rootcause_core.clients import close_clients
    from rootcause_core.clients.langfuse import langfuse_client

    first = langfuse_client(make_langfuse_settings())
    second = langfuse_client(make_langfuse_settings())
    assert first is not second
    attempted: list[Any] = []

    def shutdown(self: Any) -> None:
        attempted.append(self)
        if len(attempted) == 1:
            raise RuntimeError("exporter down")

    monkeypatch.setattr(Langfuse, "shutdown", shutdown)

    with contextlib.suppress(Exception):  # re-raising is allowed, skipping a client is not
        asyncio.run(close_clients())

    assert {id(client) for client in attempted} == {id(first), id(second)}


def test_a_failing_langfuse_shutdown_does_not_skip_closing_http_clients(
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langfuse import Langfuse

    from rootcause_core import clients
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings())
    closed: list[str] = []

    async def close_http() -> None:
        closed.append("http")

    monkeypatch.setattr(clients, "close_http_clients", close_http)
    monkeypatch.setattr(Langfuse, "shutdown", lambda self: (_ for _ in ()).throw(RuntimeError("x")))

    with contextlib.suppress(Exception):
        asyncio.run(clients.close_clients())

    assert closed == ["http"]
