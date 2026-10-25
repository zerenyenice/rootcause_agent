"""Alembic environment: one database, schemas app/catalog/audit, version table in app."""

from collections.abc import MutableMapping

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Engine

import rootcause_catalog.models  # noqa: F401  (register tables on Base.metadata)
import rootcause_connections.models  # noqa: F401
from rootcause_core.db.base import Base
from rootcause_core.db.engine import SYNC_DRIVER, make_engine, normalise_url
from rootcause_core.db.migrate import URL_ATTRIBUTE
from rootcause_core.db.schemas import ALL_SCHEMAS, APP_SCHEMA, ensure_schemas
from rootcause_core.settings import get_settings

config = context.config
target_metadata = Base.metadata


def _include_name(
    name: str | None, type_: str, parent_names: MutableMapping[str, str | None]
) -> bool:
    if type_ == "schema":
        return name in ALL_SCHEMAS
    return True


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=_include_name,  # type: ignore[arg-type]
        version_table_schema=APP_SCHEMA,
        compare_type=True,
        **kwargs,  # type: ignore[arg-type]
    )


def _engine() -> Engine:
    explicit = config.attributes.get(URL_ATTRIBUTE)
    if explicit is not None:
        from sqlalchemy import create_engine

        return create_engine(normalise_url(explicit, SYNC_DRIVER))
    return make_engine(get_settings())


def run_migrations_offline() -> None:
    raise RuntimeError("Offline migrations are not supported: schemas and extensions are required.")


def run_migrations_online() -> None:
    engine = _engine()
    try:
        with engine.connect() as connection:
            ensure_schemas(connection)
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.commit()
            _configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
