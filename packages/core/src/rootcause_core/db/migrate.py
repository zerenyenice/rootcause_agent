"""Programmatic Alembic commands against an explicit database URL."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from rootcause_core.db.engine import SYNC_DRIVER, normalise_url

ALEMBIC_DIR = Path(__file__).parent / "alembic"
URL_ATTRIBUTE = "database_url"


def make_config(url: str) -> Config:
    """Alembic config bound to ``url``; the URL is passed as an attribute (not an ini value)."""
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.attributes[URL_ATTRIBUTE] = normalise_url(url, SYNC_DRIVER).render_as_string(
        hide_password=False
    )
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(make_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(make_config(url), revision)


def current(url: str) -> None:
    command.current(make_config(url), verbose=True)


def revision(url: str, message: str, autogenerate: bool = False) -> None:
    command.revision(make_config(url), message=message, autogenerate=autogenerate)
