"""Unit tests of the persistence readiness check and setup entry points (no Postgres)."""

import asyncio
import functools
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import pytest
from psycopg import OperationalError
from psycopg.errors import UndefinedTable
from psycopg_pool import PoolTimeout

from rootcause.errors import ConfigurationError
from tests.agent_service.support import make_settings

pytestmark = pytest.mark.unit


def run_async(test: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    """Run an async test body on a fresh event loop (no async pytest plugin installed)."""

    @functools.wraps(test)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return wrapper


class _Cursor:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def fetchone(self) -> Any:
        return self._row


class _Connection:
    """Answers ``SELECT max(v)`` per table from ``versions`` (``"missing"`` raises)."""

    def __init__(self, versions: dict[str, Any]) -> None:
        self._versions = versions
        self.queried: list[str] = []

    async def execute(self, query: Any) -> _Cursor:
        text = query.as_string()
        table = next(name for name in self._versions if name in text)
        self.queried.append(table)
        value = self._versions[table]
        if value == "missing":
            raise UndefinedTable(f"relation {table} does not exist")
        return _Cursor(None if value == "no-row" else {"v": value})


class _Pool:
    def __init__(self, versions: dict[str, Any]) -> None:
        self.connection_obj = _Connection(versions)

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_Connection]:
        yield self.connection_obj


def _latest() -> dict[str, int]:
    from rootcause.agent_service.persistence import _VERSION_TABLES

    return {table: len(migrations) - 1 for table, migrations in _VERSION_TABLES}


async def _check(versions: dict[str, Any]) -> _Pool:
    from rootcause.agent_service.persistence import _require_current_schema

    pool = _Pool(versions)
    await _require_current_schema(pool)  # type: ignore[arg-type]
    return pool


@run_async
async def test_current_schema_passes_and_checks_both_components() -> None:
    pool = await _check(_latest())
    assert pool.connection_obj.queried == ["checkpoint_migrations", "store_migrations"]


@pytest.mark.parametrize("missing", ["checkpoint_migrations", "store_migrations"])
@run_async
async def test_missing_table_names_the_setup_command(missing: str) -> None:
    from rootcause.agent_service.persistence import SETUP_COMMAND

    versions: dict[str, Any] = {**_latest(), missing: "missing"}
    with pytest.raises(ConfigurationError) as raised:
        await _check(versions)
    assert SETUP_COMMAND in str(raised.value)
    assert "python -m rootcause.agent_service.persistence" in str(raised.value)


@pytest.mark.parametrize("stale", ["no-row", None, 0])
@run_async
async def test_outdated_or_empty_migration_table_is_not_ready(stale: Any) -> None:
    from rootcause.agent_service.persistence import _VERSION_TABLES, SETUP_COMMAND

    # ``0`` is outdated only when the library has more than one migration.
    if stale == 0 and len(_VERSION_TABLES[0][1]) <= 1:
        pytest.skip("library has a single checkpoint migration")
    versions: dict[str, Any] = {**_latest(), "checkpoint_migrations": stale}
    with pytest.raises(ConfigurationError, match="missing or outdated") as raised:
        await _check(versions)
    assert SETUP_COMMAND in str(raised.value)


@run_async
async def test_open_persistence_reports_an_unreachable_database_without_details() -> None:
    from rootcause.agent_service.persistence import open_persistence

    # Port 1 on loopback refuses immediately; the pool then times out waiting.
    settings = make_settings(db_connect_timeout_s=1)
    with pytest.raises(ConfigurationError) as raised:
        async with open_persistence(settings):
            pytest.fail("must not open")
    assert "ROOTCAUSE_DATABASE_URL" in str(raised.value)
    assert "127.0.0.1" not in str(raised.value)
    assert isinstance(raised.value.__cause__, (PoolTimeout, type(None)))


@run_async
async def test_setup_requires_the_migration_url() -> None:
    from rootcause.agent_service.persistence import setup_persistence

    with pytest.raises(ConfigurationError, match="ROOTCAUSE_MIGRATION_DATABASE_URL"):
        await setup_persistence(make_settings())


@run_async
async def test_setup_reports_an_unreachable_database_without_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rootcause.agent_service.persistence as persistence

    async def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OperationalError("connection to server at 10.9.8.7 failed")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", refuse)
    settings = make_settings(
        migration_database_url="postgresql://m:SENTINEL@10.9.8.7/db"
    )  # gitleaks:allow
    with pytest.raises(ConfigurationError) as raised:
        await persistence.setup_persistence(settings)
    assert "10.9.8.7" not in str(raised.value)
    assert "SENTINEL" not in str(raised.value)


def test_conninfo_drops_the_driver_suffix() -> None:
    from rootcause.agent_service.persistence import _conninfo

    url = _conninfo("postgresql+asyncpg://u:pw@h:5433/d", "VAR")
    assert url.startswith("postgresql://")
    assert "+asyncpg" not in url
    assert "u:pw@h:5433/d" in url


def test_main_returns_1_and_prints_the_error_when_unconfigured(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rootcause.agent_service.persistence import main

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    assert main() == 1
    assert "ROOTCAUSE_MIGRATION_DATABASE_URL" in capsys.readouterr().err


def test_main_returns_1_for_unexpected_errors_without_the_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import rootcause.agent_service.persistence as persistence

    async def explode(settings: Any) -> None:
        raise RuntimeError("host db.internal user admin")

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(persistence, "setup_persistence", explode)
    assert persistence.main() == 1
    captured = capsys.readouterr()
    assert "db.internal" not in captured.err + captured.out


def test_main_returns_0_when_setup_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import rootcause.agent_service.persistence as persistence

    calls: list[Any] = []

    async def ok(settings: Any) -> None:
        calls.append(settings)

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(persistence, "setup_persistence", ok)
    assert persistence.main() == 0
    assert len(calls) == 1
