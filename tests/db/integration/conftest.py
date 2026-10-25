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
from typing import Any

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


def _throwaway_database(admin: URL, *, require_pgvector: bool) -> Iterator[str]:
    """Create an empty database on ``admin``'s server, yield its URL, drop it afterwards."""
    name = f"rc_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_conninfo(admin), autocommit=True) as connection:
        if require_pgvector:
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


@pytest.fixture
def fresh_database_url(request: pytest.FixtureRequest) -> Iterator[str]:
    """URL (plain ``postgresql://``) of an empty database, dropped after the test."""
    yield from _throwaway_database(_admin_url(request), require_pgvector=True)


@pytest.fixture
def plain_database_url(request: pytest.FixtureRequest) -> Iterator[str]:
    """Like ``fresh_database_url`` but without the pgvector requirement (customer DBs)."""
    yield from _throwaway_database(_admin_url(request), require_pgvector=False)


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
    from rootcause.settings import clear_settings_cache

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
    from rootcause.db.bootstrap import bootstrap

    bootstrap(db_roles.admin_url, **db_roles.bootstrap_kwargs())
    return db_roles.migration_url


@pytest.fixture
def migrated_roles(bootstrapped_url: str, db_roles: DbRoles) -> DbRoles:
    """Bootstrapped and migrated to head as the migrator."""
    from rootcause.db.migrate import upgrade

    upgrade(bootstrapped_url, "head")
    return db_roles


# --- customer database for the connections API / Postgres executor ----------

CUSTOMER_PASSWORD = "SENTINEL-customer-pw-7"  # gitleaks:allow
ORDERS_ROWS = 300
BIG_ROWS = 2000
ORDER_STATUSES = ("closed", "open", "void")


@dataclass(frozen=True)
class CustomerDb:
    """A seeded customer database plus login roles with different privileges.

    ``reader`` can only SELECT; ``writer`` may INSERT into ``public.orders``;
    ``creator`` may CREATE in schema ``public``; ``guc_writer`` has INSERT but
    ``default_transaction_read_only = on``; ``admin_*`` is a superuser.
    """

    host: str
    port: int
    database: str
    admin_user: str
    admin_password: str
    reader: str
    writer: str
    creator: str
    guc_writer: str
    password: str
    admin_url: str

    def params(self, user: str | None = None) -> dict[str, Any]:
        """Keyword arguments for ``PostgresExecutor`` connecting as ``user`` (reader)."""
        login = user or self.reader
        is_admin = login == self.admin_user
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": login,
            "password": self.admin_password if is_admin else self.password,
            "sslmode": "disable",
        }


_CUSTOMER_SEED = (
    "CREATE TABLE public.orders (id integer PRIMARY KEY, amount numeric(12, 2) NOT NULL, "
    "status text)",
    f"INSERT INTO public.orders SELECT i, i * 1.5, (ARRAY['closed','open','void'])[1 + i % 3] "
    f"FROM generate_series(1, {ORDERS_ROWS}) AS i",
    "CREATE TABLE public.big (i integer)",
    f"INSERT INTO public.big SELECT generate_series(1, {BIG_ROWS})",
    "CREATE SCHEMA sales",
    "CREATE TABLE sales.regions (code text NOT NULL, name text)",
    "INSERT INTO sales.regions VALUES ('TR', 'Turkey'), ('DE', 'Germany')",
    "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
    "ANALYZE",
)


def _grant_reader(connection: psycopg.Connection, role: str, database: str) -> None:
    ident = sql.Identifier
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(ident(database), ident(role))
    )
    for schema in ("public", "sales"):
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident(schema), ident(role))
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                ident(schema), ident(role)
            )
        )


@pytest.fixture
def customer_db(plain_database_url: str) -> Iterator[CustomerDb]:
    """Seeded database (orders, big, sales.regions) and four login roles, dropped afterwards."""
    url = make_url(plain_database_url)
    database = url.database
    assert database is not None
    suffix = uuid.uuid4().hex[:8]
    reader, writer, creator, guc_writer = (
        f"rc_{kind}_{suffix}" for kind in ("reader", "writer", "creator", "gucw")
    )
    server = make_url(admin_url_for(plain_database_url, "postgres"))
    ident, lit = sql.Identifier, sql.Literal
    with psycopg.connect(_conninfo(server), autocommit=True) as connection:
        for role in (reader, writer, creator, guc_writer):
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    ident(role), lit(CUSTOMER_PASSWORD)
                )
            )
        connection.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                ident(guc_writer)
            )
        )
    with psycopg.connect(_conninfo(url), autocommit=True) as connection:
        for statement in _CUSTOMER_SEED:
            connection.execute(statement)
        for role in (reader, writer, creator, guc_writer):
            _grant_reader(connection, role, database)
        for role in (writer, guc_writer):
            connection.execute(sql.SQL("GRANT INSERT ON public.orders TO {}").format(ident(role)))
        connection.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(ident(creator)))
    try:
        yield CustomerDb(
            host=url.host or "127.0.0.1",
            port=url.port or 5432,
            database=database,
            admin_user=url.username or "",
            admin_password=url.password or "",
            reader=reader,
            writer=writer,
            creator=creator,
            guc_writer=guc_writer,
            password=CUSTOMER_PASSWORD,
            admin_url=plain_database_url,
        )
    finally:
        with psycopg.connect(_conninfo(server), autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident(database))
            )
            for role in (reader, writer, creator, guc_writer):
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(ident(role)))


# --- roles that are not read-only although they hold no table write grant ----
#
# Read-only rule (review finding HIGH 1): besides superuser, table write/TRUNCATE,
# sequence and CREATE grants, a role is NOT read-only when it (or a role it is a
# member of) has CREATEDB, CREATEROLE or REPLICATION; is a member of
# pg_write_server_files, pg_execute_server_program or pg_read_server_files; owns a
# non-system table, materialized view or schema (an owner can re-grant itself
# anything); or may CREATE in any database on the server.
# TEMP on the database is TOLERATED (Lead decision): Postgres grants it to PUBLIC
# by default, temp objects are session-local (never customer data), the executor's
# READ ONLY transactions refuse CREATE TEMP TABLE and the validator rejects DDL.
# The customer database keeps PUBLIC's default TEMP, so the reader has it too.
# Known gap, deliberately untested: EXECUTE on SECURITY DEFINER functions that write.

RISKY_ROLE_KINDS = (
    "createdb",
    "createrole",
    "replication",
    "write_server_files",
    "execute_server_program",
    "read_server_files",
    "table_owner",
    "matview_owner",
    "schema_owner",
    "other_db_create",
)
# Roles that hold a privilege the rule tolerates; they must still be read-only.
TOLERATED_ROLE_KINDS = ("temp_is_allowed",)
_ROLE_ATTRIBUTES = {
    "createdb": "CREATEDB",
    "createrole": "CREATEROLE",
    "replication": "REPLICATION",
}
_BUILTIN_MEMBERSHIPS = {
    "write_server_files": "pg_write_server_files",
    "execute_server_program": "pg_execute_server_program",
    "read_server_files": "pg_read_server_files",
}


@dataclass(frozen=True)
class RiskyRoles:
    """Login roles by kind (``RISKY_ROLE_KINDS``, ``TOLERATED_ROLE_KINDS``), SELECT-only grants."""

    roles: dict[str, str]
    other_database: str


def _create_risky_role(server: psycopg.Connection, kind: str, role: str) -> None:
    attribute = sql.SQL(_ROLE_ATTRIBUTES.get(kind, ""))
    server.execute(
        sql.SQL("CREATE ROLE {} LOGIN {} PASSWORD {}").format(
            sql.Identifier(role), attribute, sql.Literal(CUSTOMER_PASSWORD)
        )
    )
    builtin = _BUILTIN_MEMBERSHIPS.get(kind)
    if builtin is not None:
        server.execute(
            sql.SQL("GRANT {} TO {}").format(sql.Identifier(builtin), sql.Identifier(role))
        )


def _grant_owned_objects(db: psycopg.Connection, roles: dict[str, str], database: str) -> None:
    ident = sql.Identifier
    owner = roles["table_owner"]
    db.execute("CREATE TABLE public.owned_by_role (id integer)")
    db.execute(sql.SQL("ALTER TABLE public.owned_by_role OWNER TO {}").format(ident(owner)))
    # The owner revoked its own write grants; it can still re-grant them (or DROP).
    db.execute(
        sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.owned_by_role FROM {}").format(
            ident(owner)
        )
    )
    db.execute("CREATE MATERIALIZED VIEW public.owned_matview AS SELECT 1 AS x")
    db.execute(
        sql.SQL("ALTER MATERIALIZED VIEW public.owned_matview OWNER TO {}").format(
            ident(roles["matview_owner"])
        )
    )
    schema_owner = roles["schema_owner"]
    db.execute(sql.SQL("CREATE SCHEMA owned_schema AUTHORIZATION {}").format(ident(schema_owner)))
    db.execute(sql.SQL("REVOKE CREATE ON SCHEMA owned_schema FROM {}").format(ident(schema_owner)))
    db.execute(
        sql.SQL("GRANT TEMPORARY ON DATABASE {} TO {}").format(
            ident(database), ident(roles["temp_is_allowed"])
        )
    )


@pytest.fixture
def risky_roles(customer_db: CustomerDb) -> Iterator[RiskyRoles]:
    """One login role per ``RISKY_ROLE_KINDS``/``TOLERATED_ROLE_KINDS`` entry on the server."""
    suffix = uuid.uuid4().hex[:8]
    kinds = RISKY_ROLE_KINDS + TOLERATED_ROLE_KINDS
    roles = {kind: f"rc_{kind[:12]}_{suffix}" for kind in kinds}
    other_database = f"rc_other_{suffix}"
    server_url = make_url(admin_url_for(customer_db.admin_url, "postgres"))
    database_url = make_url(customer_db.admin_url)
    ident = sql.Identifier
    with psycopg.connect(_conninfo(server_url), autocommit=True) as server:
        for kind, role in roles.items():
            _create_risky_role(server, kind, role)
        server.execute(sql.SQL("CREATE DATABASE {}").format(ident(other_database)))
        server.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                ident(other_database), ident(roles["other_db_create"])
            )
        )
    with psycopg.connect(_conninfo(database_url), autocommit=True) as db:
        for role in roles.values():
            _grant_reader(db, role, customer_db.database)
        _grant_owned_objects(db, roles, customer_db.database)
    try:
        yield RiskyRoles(roles=roles, other_database=other_database)
    finally:
        with psycopg.connect(_conninfo(server_url), autocommit=True) as server:
            server.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident(other_database))
            )
        with psycopg.connect(_conninfo(database_url), autocommit=True) as db:
            for role in roles.values():
                db.execute(sql.SQL("DROP OWNED BY {}").format(ident(role)))
        with psycopg.connect(_conninfo(server_url), autocommit=True) as server:
            for role in roles.values():
                server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(ident(role)))


# --- connections API against the customer database ----------------------------

CONNECTIONS_PATH = "/api/v1/connections"
UNREACHABLE_APP_DB = "postgresql://u:p@127.0.0.1:1/db"


@dataclass(frozen=True)
class ConnectionsApi:
    """App client; connection routes use an in-memory repository, only the customer DB is real."""

    client: Any  # fastapi.testclient.TestClient
    repository: Any  # rootcause.connections.repository.InMemoryConnectionRepository
    customer: CustomerDb

    def register(self, user: str, **overrides: Any) -> str:
        """Store a Postgres connection to the customer database as ``user``; returns its id."""
        body = {
            "name": f"customer as {user}",
            "kind": "postgres",
            "host": self.customer.host,
            "port": self.customer.port,
            "database": self.customer.database,
            "user": user,
            "password": self.customer.password,
            "sslmode": "disable",
            **overrides,
        }
        response = self.client.post(CONNECTIONS_PATH, json=body)
        assert response.status_code == 201, response.text
        return str(response.json()["data"]["id"])

    def verified_at(self, connection_id: str) -> Any:
        import asyncio

        record = asyncio.run(self.repository.get(uuid.UUID(connection_id)))
        assert record is not None
        return record.read_only_verified_at


@pytest.fixture
def make_connections_api(
    customer_db: CustomerDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> Iterator[Any]:
    """Factory: ``make_connections_api(**settings)`` -> :class:`ConnectionsApi`.

    Keyword settings map to ``ROOTCAUSE_<NAME>`` (e.g. ``introspect_max_tables="5"``).
    """
    from contextlib import ExitStack

    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from rootcause.api.main import create_app
    from rootcause.api.routes.connections import get_connection_repository
    from rootcause.connections.repository import InMemoryConnectionRepository
    from rootcause.settings import clear_settings_cache

    for name in [key for key in os.environ if key.upper().startswith("ROOTCAUSE_")]:
        if not name.upper().startswith("ROOTCAUSE_TEST_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    stack = ExitStack()

    def _make(**settings: str) -> ConnectionsApi:
        monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", UNREACHABLE_APP_DB)
        monkeypatch.setenv("ROOTCAUSE_SECRET_KEY", Fernet.generate_key().decode("ascii"))
        for key, value in settings.items():
            monkeypatch.setenv(f"ROOTCAUSE_{key.upper()}", value)
        clear_settings_cache()
        app = create_app()
        repository = InMemoryConnectionRepository()
        app.dependency_overrides[get_connection_repository] = lambda: repository
        client = stack.enter_context(TestClient(app))
        return ConnectionsApi(client=client, repository=repository, customer=customer_db)

    with stack:
        yield _make
    clear_settings_cache()
