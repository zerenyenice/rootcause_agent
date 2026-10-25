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
from dataclasses import dataclass

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


# --- three-role fixtures (plan amendment 2) ---------------------------------


def admin_url_for(database_url: str, database: str | None = None) -> str:
    """Superuser URL for ``database_url``'s server (optionally another database)."""
    url = make_url(database_url)
    if database is not None:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


ROLE_ENV = {
    "owner": "ROOTCAUSE_DB_OWNER_ROLE",
    "migrator": "ROOTCAUSE_DB_MIGRATOR_ROLE",
    "app": "ROOTCAUSE_DB_APP_ROLE",
}


@dataclass(frozen=True)
class DbRoles:
    """Owner (NOLOGIN), migrator (member of owner) and runtime app role, per test."""

    owner: str
    migrator: str
    app: str
    password: str
    admin_url: str  # superuser URL of the fresh database
    migration_url: str  # connects as the migrator
    app_url: str  # connects as the app role
    admin_user: str

    def bootstrap_kwargs(self) -> dict[str, str]:
        return {"owner_role": self.owner, "migrator_role": self.migrator, "app_role": self.app}


def _create_roles(server: URL, owner: str, migrator: str, app: str, password: str) -> None:
    ident, lit = sql.Identifier, sql.Literal
    with psycopg.connect(_conninfo(server), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(ident(owner)))
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                ident(migrator), lit(password), ident(owner)
            )
        )
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(ident(app), lit(password))
        )


@pytest.fixture
def db_roles(fresh_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[DbRoles]:
    """Three roles on the fresh database's server, plus the env a deployment would set.

    Sets ROOTCAUSE_DATABASE_URL (app), ROOTCAUSE_MIGRATION_DATABASE_URL (migrator),
    ROOTCAUSE_ADMIN_DATABASE_URL (superuser) and the three ROOTCAUSE_DB_*_ROLE names.
    """
    from rootcause_core.settings import clear_settings_cache

    suffix = uuid.uuid4().hex[:10]
    owner, migrator, app = (f"rc_{kind}_{suffix}" for kind in ("owner", "migrator", "app"))
    password = "role-pw-9f2"
    fresh = make_url(fresh_database_url)
    database = fresh.database
    assert database is not None
    server = make_url(admin_url_for(fresh_database_url, "postgres"))
    _create_roles(server, owner, migrator, app, password)
    roles = DbRoles(
        owner=owner,
        migrator=migrator,
        app=app,
        password=password,
        admin_url=fresh_database_url,
        migration_url=fresh.set(username=migrator, password=password).render_as_string(
            hide_password=False
        ),
        app_url=fresh.set(username=app, password=password).render_as_string(hide_password=False),
        admin_user=fresh.username or "",
    )
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", roles.app_url)
    monkeypatch.setenv("ROOTCAUSE_MIGRATION_DATABASE_URL", roles.migration_url)
    monkeypatch.setenv("ROOTCAUSE_ADMIN_DATABASE_URL", roles.admin_url)
    for kind, variable in ROLE_ENV.items():
        monkeypatch.setenv(variable, getattr(roles, kind))
    clear_settings_cache()
    try:
        yield roles
    finally:
        with psycopg.connect(_conninfo(server), autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
            )
            for role in (app, migrator, owner):
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.fixture
def bootstrapped_url(db_roles: DbRoles) -> str:
    """Bootstrapped database; the URL connects as the migrator (member of the owner role)."""
    from rootcause_db.bootstrap import bootstrap

    bootstrap(db_roles.admin_url, **db_roles.bootstrap_kwargs())
    return db_roles.migration_url


@pytest.fixture
def migrated_roles(bootstrapped_url: str, db_roles: DbRoles) -> DbRoles:
    """Bootstrapped and migrated to head as the migrator."""
    from rootcause_db.migrate import upgrade

    upgrade(bootstrapped_url, "head")
    return db_roles
