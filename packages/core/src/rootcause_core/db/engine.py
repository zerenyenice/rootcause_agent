"""Engine factories for the application database (no connection on construction)."""

from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Settings

SYNC_DRIVER = "postgresql+psycopg"
ASYNC_DRIVER = "postgresql+asyncpg"
POSTGRES_BACKENDS = ("postgres", "postgresql")
DATABASE_URL_VARIABLE = "ROOTCAUSE_DATABASE_URL"

# Driver-specific name of the connect timeout (seconds) keyword.
SYNC_CONNECT_TIMEOUT_ARG = "connect_timeout"  # libpq / psycopg
ASYNC_CONNECT_TIMEOUT_ARG = "timeout"  # asyncpg.connect


def _invalid_url(source: str) -> ConfigurationError:
    # Never echo the URL: it carries the password.
    return ConfigurationError(
        f"{source} must be a valid PostgreSQL URL (postgres://user:password@host:port/database).",
        variables=(source,),
    )


def normalise_url(raw_url: str, driver: str, *, source: str = DATABASE_URL_VARIABLE) -> URL:
    """Return ``raw_url`` rewritten to use ``driver``; query parameters are kept.

    ``source`` names where the URL came from (an environment variable or a
    command-line option) so the error points at the right place.

    Raises:
        ConfigurationError: when the URL is malformed or not a PostgreSQL URL.
            The message never contains the URL.
    """
    try:
        url = make_url(raw_url)
    except (ArgumentError, ValueError):
        # ValueError: e.g. a non-numeric port. ``from None`` drops the original
        # exception, whose message may quote the URL.
        raise _invalid_url(source) from None
    if url.get_backend_name() not in POSTGRES_BACKENDS:
        raise _invalid_url(source)
    return url.set(drivername=driver)


def _pool_options(settings: Settings) -> dict[str, Any]:
    return {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_recycle": settings.db_pool_recycle_s,
    }


def make_engine(settings: Settings) -> Engine:
    """Sync (psycopg) engine for admin tasks, pooled per ``settings``."""
    url = normalise_url(settings.database_url.get_secret_value(), SYNC_DRIVER)
    return create_engine(
        url,
        connect_args={SYNC_CONNECT_TIMEOUT_ARG: settings.db_connect_timeout_s},
        **_pool_options(settings),
    )


def make_async_engine(settings: Settings) -> AsyncEngine:
    """Async (asyncpg) engine for the API, pooled per ``settings``."""
    url = normalise_url(settings.database_url.get_secret_value(), ASYNC_DRIVER)
    return create_async_engine(
        url,
        connect_args={ASYNC_CONNECT_TIMEOUT_ARG: settings.db_connect_timeout_s},
        **_pool_options(settings),
    )
