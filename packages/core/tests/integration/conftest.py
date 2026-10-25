"""Throwaway Postgres for migration tests.

Server selection, in order:
1. ``ROOTCAUSE_TEST_ADMIN_URL`` (e.g. the CI service container): a superuser URL.
2. A local ``pg_ctl`` (``PG_CTL`` env var, ``PATH`` or Homebrew) started by
   ``pytest-postgresql``.
Otherwise the tests skip with an explicit reason. Each test gets its own database.
"""

import glob
import os
import shutil
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pytest_postgresql import factories
from sqlalchemy.engine import URL, make_url

ADMIN_URL_ENV = "ROOTCAUSE_TEST_ADMIN_URL"
HOMEBREW_PATTERNS = (
    "/opt/homebrew/opt/postgresql@*/bin/pg_ctl",
    "/usr/local/opt/postgresql@*/bin/pg_ctl",
    "/usr/lib/postgresql/*/bin/pg_ctl",
)


def _find_pg_ctl() -> str | None:
    configured = os.environ.get("PG_CTL")
    if configured and os.path.exists(configured):
        return configured
    on_path = shutil.which("pg_ctl")
    if on_path:
        return on_path
    for pattern in HOMEBREW_PATTERNS:
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return matches[0]
    return None


PG_CTL = _find_pg_ctl()

# Registered unconditionally; only instantiated when PG_CTL is available.
postgresql_proc = factories.postgresql_proc(executable=PG_CTL or "pg_ctl", port=None)


def _admin_url(request: pytest.FixtureRequest) -> URL:
    external = os.environ.get(ADMIN_URL_ENV)
    if external:
        return make_url(external).set(drivername="postgresql")
    if PG_CTL is None:
        pytest.skip(
            f"no Postgres available: set {ADMIN_URL_ENV} or install PostgreSQL "
            "(pg_ctl not found via PG_CTL, PATH or Homebrew)"
        )
    proc = request.getfixturevalue("postgresql_proc")
    return URL.create(
        "postgresql",
        username=proc.user,
        password=proc.password or None,
        host=proc.host,
        port=proc.port,
        database="postgres",
    )


def _conninfo(url: URL) -> str:
    return make_conninfo(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        dbname=url.database,
    )


@pytest.fixture
def fresh_database_url(request: pytest.FixtureRequest) -> Iterator[str]:
    """URL (plain ``postgresql://``) of an empty database, dropped after the test."""
    admin = _admin_url(request)
    name = f"rc_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_conninfo(admin), autocommit=True) as connection:
        available = connection.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
        ).fetchone()
        if available is None:
            pytest.skip(
                "pgvector is not installed for this Postgres server "
                "(no 'vector' in pg_available_extensions)"
            )
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield admin.set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(_conninfo(admin), autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
