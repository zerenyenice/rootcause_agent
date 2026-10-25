"""``make_engine`` / ``make_async_engine`` normalise the URL driver without connecting."""

from typing import cast

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.pool import QueuePool

from rootcause.errors import ConfigurationError
from rootcause.settings import Settings

PASSWORD = "s3cr3t-pw"
POSTGRES_URLS = [
    f"postgres://user:{PASSWORD}@db.invalid:6543/rootcause",
    f"postgresql://user:{PASSWORD}@db.invalid:6543/rootcause",
    f"postgresql+psycopg://user:{PASSWORD}@db.invalid:6543/rootcause",
    f"postgresql+psycopg2://user:{PASSWORD}@db.invalid:6543/rootcause",
    f"postgresql+asyncpg://user:{PASSWORD}@db.invalid:6543/rootcause",
]


def _settings(url: str) -> Settings:
    return Settings(database_url=url)


def _assert_target_preserved(url) -> None:
    assert url.username == "user"
    assert url.password == PASSWORD
    assert url.host == "db.invalid"
    assert url.port == 6543
    assert url.database == "rootcause"


@pytest.mark.parametrize("raw_url", POSTGRES_URLS)
def test_make_engine_uses_the_psycopg_driver(raw_url):
    from rootcause.db.engine import make_engine

    engine = make_engine(_settings(raw_url))

    assert engine.url.drivername == "postgresql+psycopg"
    _assert_target_preserved(engine.url)


@pytest.mark.parametrize("raw_url", POSTGRES_URLS)
def test_make_async_engine_uses_the_asyncpg_driver(raw_url):
    from rootcause.db.engine import make_async_engine

    engine = make_async_engine(_settings(raw_url))

    assert engine.url.drivername == "postgresql+asyncpg"
    _assert_target_preserved(engine.url)


def test_engines_do_not_connect_at_construction():
    from rootcause.db.engine import make_async_engine, make_engine

    settings = _settings(POSTGRES_URLS[0])

    # db.invalid never resolves; construction must stay lazy.
    make_engine(settings)
    make_async_engine(settings)


def test_make_engine_enables_pool_pre_ping():
    from rootcause.db.engine import make_engine

    engine = make_engine(_settings(POSTGRES_URLS[1]))

    assert engine.pool._pre_ping is True  # noqa: SLF001


def test_make_async_engine_enables_pool_pre_ping():
    from rootcause.db.engine import make_async_engine

    engine = make_async_engine(_settings(POSTGRES_URLS[1]))

    assert engine.sync_engine.pool._pre_ping is True  # noqa: SLF001


@pytest.mark.parametrize("factory_name", ["make_engine", "make_async_engine"])
def test_non_postgres_url_is_rejected_without_leaking_the_secret(factory_name):
    from rootcause.db import engine as engine_module

    factory = getattr(engine_module, factory_name)

    with pytest.raises(ConfigurationError) as raised:
        factory(_settings(f"mysql://user:{PASSWORD}@db.invalid/rootcause"))

    assert PASSWORD not in str(raised.value)


def test_url_with_query_parameters_keeps_them():
    from rootcause.db.engine import make_engine

    engine = make_engine(_settings(POSTGRES_URLS[1] + "?application_name=rootcause"))

    assert make_url(engine.url.render_as_string(hide_password=False)).query == {
        "application_name": "rootcause"
    }


# --- amendment: malformed URLs and pool settings -----------------------------


@pytest.mark.parametrize("factory_name", ["make_engine", "make_async_engine"])
def test_malformed_port_raises_configuration_error_without_leaking_the_secret(factory_name):
    from rootcause.db import engine as engine_module

    factory = getattr(engine_module, factory_name)

    with pytest.raises(ConfigurationError) as raised:
        factory(_settings("postgresql://u:pw@h:abc/db"))

    assert "pw" not in str(raised.value).replace("postgresql", "")


def test_pool_settings_default_values():
    settings = _settings(POSTGRES_URLS[1])

    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 10
    assert settings.db_pool_recycle_s == 1800
    assert settings.db_connect_timeout_s == 5


def test_pool_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", POSTGRES_URLS[1])
    monkeypatch.setenv("ROOTCAUSE_DB_POOL_SIZE", "7")
    monkeypatch.setenv("ROOTCAUSE_DB_MAX_OVERFLOW", "3")
    monkeypatch.setenv("ROOTCAUSE_DB_POOL_RECYCLE_S", "60")
    monkeypatch.setenv("ROOTCAUSE_DB_CONNECT_TIMEOUT_S", "2")

    settings = Settings()

    assert (
        settings.db_pool_size,
        settings.db_max_overflow,
        settings.db_pool_recycle_s,
        settings.db_connect_timeout_s,
    ) == (7, 3, 60, 2)


@pytest.mark.parametrize("field", ["db_pool_size", "db_pool_recycle_s", "db_connect_timeout_s"])
def test_non_positive_pool_settings_are_rejected(field):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings.model_validate({"database_url": POSTGRES_URLS[1], field: 0})


def test_make_engine_applies_pool_settings():
    from rootcause.db.engine import make_engine

    settings = Settings(
        database_url=POSTGRES_URLS[1],
        db_pool_size=7,
        db_max_overflow=3,
        db_pool_recycle_s=60,
    )

    pool = cast(QueuePool, make_engine(settings).pool)

    assert pool.size() == 7
    assert pool._max_overflow == 3  # noqa: SLF001
    assert pool._recycle == 60  # noqa: SLF001


def test_make_async_engine_applies_pool_settings():
    from rootcause.db.engine import make_async_engine

    settings = Settings(
        database_url=POSTGRES_URLS[1], db_pool_size=7, db_max_overflow=3, db_pool_recycle_s=60
    )

    pool = cast(QueuePool, make_async_engine(settings).sync_engine.pool)

    assert pool.size() == 7
    assert pool._max_overflow == 3  # noqa: SLF001
    assert pool._recycle == 60  # noqa: SLF001


def test_default_engine_uses_default_pool_size():
    from rootcause.db.engine import make_engine

    pool = cast(QueuePool, make_engine(_settings(POSTGRES_URLS[1])).pool)

    assert pool.size() == 5
    assert pool._max_overflow == 10  # noqa: SLF001
    assert pool._recycle == 1800  # noqa: SLF001


@pytest.mark.parametrize("factory_name", ["make_engine", "make_async_engine"])
def test_connect_timeout_is_passed_to_the_driver(factory_name):
    from sqlalchemy import event

    from rootcause.db import engine as engine_module

    created = getattr(engine_module, factory_name)(
        Settings(database_url=POSTGRES_URLS[1], db_connect_timeout_s=3)
    )
    sync_engine = getattr(created, "sync_engine", created)
    captured: dict[str, object] = {}

    class StopConnecting(Exception):
        pass

    @event.listens_for(sync_engine, "do_connect")
    def _capture(dialect, conn_rec, cargs, cparams):
        captured.update(cparams)
        raise StopConnecting

    with pytest.raises(StopConnecting), sync_engine.connect():
        pass

    timeout_keys = {"connect_timeout", "timeout"}
    assert any(captured.get(key) == 3 for key in timeout_keys), captured
