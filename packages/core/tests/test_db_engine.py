"""``make_engine`` / ``make_async_engine`` normalise the URL driver without connecting."""

import pytest
from sqlalchemy.engine import make_url

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Settings

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
    from rootcause_core.db.engine import make_engine

    engine = make_engine(_settings(raw_url))

    assert engine.url.drivername == "postgresql+psycopg"
    _assert_target_preserved(engine.url)


@pytest.mark.parametrize("raw_url", POSTGRES_URLS)
def test_make_async_engine_uses_the_asyncpg_driver(raw_url):
    from rootcause_core.db.engine import make_async_engine

    engine = make_async_engine(_settings(raw_url))

    assert engine.url.drivername == "postgresql+asyncpg"
    _assert_target_preserved(engine.url)


def test_engines_do_not_connect_at_construction():
    from rootcause_core.db.engine import make_async_engine, make_engine

    settings = _settings(POSTGRES_URLS[0])

    # db.invalid never resolves; construction must stay lazy.
    make_engine(settings)
    make_async_engine(settings)


def test_make_engine_enables_pool_pre_ping():
    from rootcause_core.db.engine import make_engine

    engine = make_engine(_settings(POSTGRES_URLS[1]))

    assert engine.pool._pre_ping is True  # noqa: SLF001


def test_make_async_engine_enables_pool_pre_ping():
    from rootcause_core.db.engine import make_async_engine

    engine = make_async_engine(_settings(POSTGRES_URLS[1]))

    assert engine.sync_engine.pool._pre_ping is True  # noqa: SLF001


@pytest.mark.parametrize("factory_name", ["make_engine", "make_async_engine"])
def test_non_postgres_url_is_rejected_without_leaking_the_secret(factory_name):
    from rootcause_core.db import engine as engine_module

    factory = getattr(engine_module, factory_name)

    with pytest.raises(ConfigurationError) as raised:
        factory(_settings(f"mysql://user:{PASSWORD}@db.invalid/rootcause"))

    assert PASSWORD not in str(raised.value)


def test_url_with_query_parameters_keeps_them():
    from rootcause_core.db.engine import make_engine

    engine = make_engine(_settings(POSTGRES_URLS[1] + "?application_name=rootcause"))

    assert make_url(engine.url.render_as_string(hide_password=False)).query == {
        "application_name": "rootcause"
    }
