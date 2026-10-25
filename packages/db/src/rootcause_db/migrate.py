"""Programmatic Alembic commands against an explicit database URL."""

import threading
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import NullPool

from rootcause_core.db.engine import SYNC_CONNECT_TIMEOUT_ARG, SYNC_DRIVER, normalise_url
from rootcause_db.names import MIGRATION_URL_VARIABLE

ALEMBIC_DIR = Path(__file__).parent / "alembic"
URL_ATTRIBUTE = "database_url"
# ConfigParser interpolation: "%%" is a literal "%".
FILE_TEMPLATE = "%%(rev)s_%%(slug)s"
MIGRATION_CONNECT_TIMEOUT_S = 10

# Alembic's ``context`` is a process-global proxy, so two commands running in
# threads of one process would overwrite each other's environment. This lock
# serialises them in-process; env.py's advisory lock serialises processes.
_COMMAND_LOCK = threading.Lock()


def make_migration_engine(url: str, *, source: str = MIGRATION_URL_VARIABLE) -> Engine:
    """Unpooled psycopg engine for one-shot admin work (migrations, bootstrap).

    ``source`` names where ``url`` came from, for the error message.

    Raises:
        ConfigurationError: when ``url`` is not a valid PostgreSQL URL.
    """
    return create_engine(
        normalise_url(url, SYNC_DRIVER, source=source),
        poolclass=NullPool,
        connect_args={SYNC_CONNECT_TIMEOUT_ARG: MIGRATION_CONNECT_TIMEOUT_S},
    )


def make_config(url: str) -> Config:
    """Alembic config bound to ``url``; the URL is passed as an attribute (not an ini value)."""
    normalise_url(url, SYNC_DRIVER, source=MIGRATION_URL_VARIABLE)  # fail fast, before env.py
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("file_template", FILE_TEMPLATE)
    config.attributes[URL_ATTRIBUTE] = url
    return config


def upgrade(url: str, revision: str = "head") -> None:
    config = make_config(url)
    with _COMMAND_LOCK:
        command.upgrade(config, revision)


def downgrade(url: str, revision: str) -> None:
    config = make_config(url)
    with _COMMAND_LOCK:
        command.downgrade(config, revision)


def current(url: str) -> None:
    config = make_config(url)
    with _COMMAND_LOCK:
        command.current(config)


def revision(url: str, message: str, autogenerate: bool = False) -> None:
    config = make_config(url)
    with _COMMAND_LOCK:
        command.revision(config, message=message, autogenerate=autogenerate)
