"""Engine factories for the application database (no connection on construction)."""

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


def _invalid_url() -> ConfigurationError:
    # Never echo the URL: it carries the password.
    return ConfigurationError(
        f"{DATABASE_URL_VARIABLE} must be a PostgreSQL URL (postgres:// or postgresql://).",
        variables=(DATABASE_URL_VARIABLE,),
    )


def normalise_url(raw_url: str, driver: str) -> URL:
    """Return ``raw_url`` rewritten to use ``driver``; query parameters are kept."""
    try:
        url = make_url(raw_url)
    except ArgumentError:
        raise _invalid_url() from None
    if url.get_backend_name() not in POSTGRES_BACKENDS:
        raise _invalid_url()
    return url.set(drivername=driver)


def make_engine(settings: Settings) -> Engine:
    """Sync (psycopg) engine for migrations and admin tasks."""
    url = normalise_url(settings.database_url.get_secret_value(), SYNC_DRIVER)
    return create_engine(url, pool_pre_ping=True)


def make_async_engine(settings: Settings) -> AsyncEngine:
    """Async (asyncpg) engine for the API."""
    url = normalise_url(settings.database_url.get_secret_value(), ASYNC_DRIVER)
    return create_async_engine(url, pool_pre_ping=True)
