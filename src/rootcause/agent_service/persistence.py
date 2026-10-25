"""Postgres persistence of the agent service: LangGraph checkpointer and store.

Three-role database (docs/adr/0007): the runtime (app) role has no ``CREATE`` on
any schema, so it cannot run the LangGraph ``setup()`` DDL itself. The tables are
created once, before the service starts, by::

    python -m rootcause.agent_service.persistence

which connects with ``ROOTCAUSE_MIGRATION_DATABASE_URL`` (the migrator role),
assumes the NOLOGIN owner role and creates them in the ``app`` schema, where
bootstrap's default privileges give the app role DML on them. The service then
connects as the app role with ``search_path=app`` and only checks that the
tables are current (:func:`open_persistence`).
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection, OperationalError, sql
from psycopg.errors import UndefinedTable
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from rootcause.db.engine import DATABASE_URL_VARIABLE, normalise_url
from rootcause.db.names import MIGRATION_URL_VARIABLE
from rootcause.db.schemas import APP_SCHEMA
from rootcause.errors import ConfigurationError
from rootcause.logging import configure_logging, get_logger
from rootcause.settings import Settings, get_settings

LIBPQ_DRIVER: Final = "postgresql"
SEARCH_PATH_OPTION: Final = f"-c search_path={APP_SCHEMA}"
SETUP_COMMAND: Final = "python -m rootcause.agent_service.persistence"
# (migration-version table, the library's migration list) per persistence component.
_VERSION_TABLES: Final[tuple[tuple[str, Sequence[Any]], ...]] = (
    ("checkpoint_migrations", AsyncPostgresSaver.MIGRATIONS),
    ("store_migrations", AsyncPostgresStore.MIGRATIONS),
)
_NOT_READY = (
    "The agent persistence tables are missing or outdated. Run `{command}` "
    f"(it connects with {MIGRATION_URL_VARIABLE}) after the database migrations."
)
_UNREACHABLE = (
    "Could not connect to the agent persistence database. Check that the server is "
    "reachable and that {variable} is correct; details are omitted because they can "
    "contain the host and user."
)

_logger = get_logger(__name__)

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


@dataclass(frozen=True)
class Persistence:
    """The opened checkpointer and store, sharing one connection pool."""

    checkpointer: AsyncPostgresSaver
    store: AsyncPostgresStore


def _conninfo(url: str, variable: str) -> str:
    """libpq URL from a settings URL (``+driver`` suffix dropped); never logged."""
    return normalise_url(url, LIBPQ_DRIVER, source=variable).render_as_string(hide_password=False)


def _connect_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": SEARCH_PATH_OPTION,
        "connect_timeout": settings.db_connect_timeout_s,
    }


async def _require_current_schema(pool: Pool) -> None:
    """Raise unless every LangGraph migration has been applied in the ``app`` schema."""
    async with pool.connection() as connection:
        for table, migrations in _VERSION_TABLES:
            query = sql.SQL("SELECT max(v) AS v FROM {}").format(sql.Identifier(table))
            try:
                row = await (await connection.execute(query)).fetchone()
            except UndefinedTable:
                raise ConfigurationError(_NOT_READY.format(command=SETUP_COMMAND)) from None
            version = None if row is None else row["v"]
            if version is None or version < len(migrations) - 1:
                raise ConfigurationError(_NOT_READY.format(command=SETUP_COMMAND))


@asynccontextmanager
async def open_persistence(settings: Settings) -> AsyncIterator[Persistence]:
    """Open the pooled checkpointer and store as the app role (service lifespan).

    Raises:
        ConfigurationError: the database is unreachable or the tables are not set up.
    """
    pool: Pool = AsyncConnectionPool(
        _conninfo(settings.database_url.get_secret_value(), DATABASE_URL_VARIABLE),
        min_size=1,
        max_size=settings.db_pool_size,
        kwargs=_connect_kwargs(settings),
        open=False,
    )
    try:
        await pool.open(wait=True, timeout=settings.db_connect_timeout_s)
    except PoolTimeout:
        await pool.close()
        raise ConfigurationError(_UNREACHABLE.format(variable=DATABASE_URL_VARIABLE)) from None
    try:
        await _require_current_schema(pool)
        yield Persistence(checkpointer=AsyncPostgresSaver(pool), store=AsyncPostgresStore(pool))
    finally:
        await pool.close()


async def setup_persistence(settings: Settings) -> None:
    """Create or migrate the checkpointer and store tables as the owner role.

    Idempotent. Runs the libraries' own ``setup()`` (autocommit, so their
    ``CREATE INDEX CONCURRENTLY`` statements work) under ``SET ROLE`` to the
    owner role, so the tables are owned like every migrated table.

    Raises:
        ConfigurationError: the migration URL is unset or the database is unreachable.
    """
    if settings.migration_database_url is None:
        raise ConfigurationError(
            f"{MIGRATION_URL_VARIABLE} is not set; the setup connects as the migrator role.",
            variables=(MIGRATION_URL_VARIABLE,),
        )
    conninfo = _conninfo(settings.migration_database_url.get_secret_value(), MIGRATION_URL_VARIABLE)
    try:
        connection = await AsyncConnection.connect(conninfo, **_connect_kwargs(settings))
    except OperationalError:
        raise ConfigurationError(_UNREACHABLE.format(variable=MIGRATION_URL_VARIABLE)) from None
    async with connection:
        role = sql.Identifier(settings.db_owner_role)
        await connection.execute(sql.SQL("SET ROLE {}").format(role))
        await AsyncPostgresSaver(connection).setup()
        await AsyncPostgresStore(connection).setup()


def main() -> int:
    """Entry point of ``python -m rootcause.agent_service.persistence``."""
    try:
        settings = get_settings()
        configure_logging(settings)
        asyncio.run(setup_persistence(settings))
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - report the type only: messages name hosts
        _logger.error("agent_persistence_setup_failed", error_type=type(error).__name__)
        return 1
    _logger.info("agent_persistence_ready", schema=APP_SCHEMA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
