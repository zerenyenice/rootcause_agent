"""``bootstrap`` (superuser) prepares the database for the three-role model."""

from typing import Any

import psycopg
import pytest
import sqlalchemy as sa

from rootcause_core.errors import ConfigurationError

pytestmark = pytest.mark.integration

SCHEMAS = ("app", "catalog", "audit")


def _sync_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _scalars(url: str, statement: str, **params: object) -> list[Any]:
    engine = sa.create_engine(_sync_url(url))
    try:
        with engine.connect() as connection:
            return list(connection.execute(sa.text(statement), params).scalars())
    finally:
        engine.dispose()


def _exec(url: str, statement: str) -> None:
    engine = sa.create_engine(_sync_url(url))
    try:
        with engine.connect() as connection:
            connection.execute(sa.text(statement))
    finally:
        engine.dispose()


def _rows(url: str, statement: str, **params: object) -> list[Any]:
    engine = sa.create_engine(_sync_url(url))
    try:
        with engine.connect() as connection:
            return [tuple(r) for r in connection.execute(sa.text(statement), params)]
    finally:
        engine.dispose()


def test_bootstrap_creates_extension_and_schemas_owned_by_the_owner_role(db_roles):
    from rootcause_db.bootstrap import bootstrap

    bootstrap(db_roles.admin_url, **db_roles.bootstrap_kwargs())

    assert "vector" in _scalars(db_roles.admin_url, "SELECT extname FROM pg_extension")
    owners = dict(
        _rows(
            db_roles.admin_url,
            "SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace "
            "WHERE nspname = ANY(:schemas)",
            schemas=list(SCHEMAS),
        )
    )
    assert owners == dict.fromkeys(SCHEMAS, db_roles.owner)


def test_bootstrap_is_idempotent(db_roles):
    from rootcause_db.bootstrap import bootstrap

    bootstrap(db_roles.admin_url, **db_roles.bootstrap_kwargs())
    bootstrap(db_roles.admin_url, **db_roles.bootstrap_kwargs())

    assert "vector" in _scalars(db_roles.admin_url, "SELECT extname FROM pg_extension")


def test_app_role_gets_usage_but_not_create_on_the_schemas(bootstrapped_url, db_roles):
    for schema in SCHEMAS:
        usage, create = _rows(
            db_roles.admin_url,
            "SELECT has_schema_privilege(:r, :s, 'USAGE'), has_schema_privilege(:r, :s, 'CREATE')",
            r=db_roles.app,
            s=schema,
        )[0]
        assert (schema, usage, create) == (schema, True, False)


def test_migrator_can_set_role_to_the_owner_but_app_cannot(bootstrapped_url, db_roles):
    _exec(db_roles.migration_url, f"SET ROLE {db_roles.owner}")

    with pytest.raises(sa.exc.DBAPIError):
        _exec(db_roles.app_url, f"SET ROLE {db_roles.owner}")


def test_public_schema_is_not_creatable_by_public(bootstrapped_url, db_roles):
    creatable = _scalars(
        db_roles.admin_url, "SELECT has_schema_privilege(:r, 'public', 'CREATE')", r=db_roles.app
    )
    assert creatable == [False]


def test_app_role_does_not_own_the_database(bootstrapped_url, db_roles):
    owner = _scalars(
        db_roles.admin_url,
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()",
    )
    assert owner != [db_roles.app]
    assert owner != [db_roles.migrator]


def test_migrator_upgrade_succeeds_after_bootstrap(bootstrapped_url, db_roles):
    from rootcause_db.migrate import upgrade

    upgrade(bootstrapped_url, "head")

    assert _scalars(bootstrapped_url, "SELECT count(*) FROM app.alembic_version") == [1]
    assert _scalars(
        bootstrapped_url, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    ) == [False]


def test_upgrade_without_bootstrap_explains_bootstrap(db_roles):
    from rootcause_db.migrate import upgrade

    with pytest.raises(Exception) as raised:  # noqa: PT011
        upgrade(db_roles.migration_url, "head")

    assert "bootstrap" in str(raised.value).lower()
    assert not isinstance(raised.value, (sa.exc.DBAPIError, psycopg.Error)), (
        "must fail fast with a clear message, not a raw permission error"
    )
    assert db_roles.password not in str(raised.value)


# --- refusals ---------------------------------------------------------------

UNSAFE_ROLE_DDL = {
    "superuser": "CREATE ROLE {name} LOGIN SUPERUSER",
    "bypassrls": "CREATE ROLE {name} LOGIN BYPASSRLS",
}


def _admin_exec(db_roles, statement: str) -> None:
    with psycopg.connect(db_roles.admin_url, autocommit=True) as connection:
        connection.execute(statement)


@pytest.fixture
def unsafe_role(request, db_roles):
    """Name of a role bootstrap must refuse, by kind."""
    kind = request.param
    if kind == "admin":
        yield db_roles.admin_user
        return
    if kind == "pg_role":
        yield "pg_read_all_data"
        return
    name = f"rc_unsafe_{kind}_{db_roles.owner[-10:]}"
    _admin_exec(db_roles, UNSAFE_ROLE_DDL[kind].format(name=name))
    try:
        yield name
    finally:
        _admin_exec(db_roles, f"DROP ROLE IF EXISTS {name}")


def _no_grants_made(db_roles, role: str) -> None:
    default_acls = _scalars(db_roles.admin_url, "SELECT count(*) FROM pg_default_acl")
    assert default_acls == [0], "no default privileges may be set before the refusal"
    acls = _scalars(
        db_roles.admin_url,
        "SELECT coalesce(nspacl::text, '') FROM pg_namespace WHERE nspname = ANY(:s)",
        s=list(SCHEMAS),
    )
    assert all(role not in acl for acl in acls)


@pytest.mark.parametrize("slot", ["app_role", "migrator_role"])
@pytest.mark.parametrize(
    "unsafe_role", ["superuser", "bypassrls", "pg_role", "admin"], indirect=True
)
def test_bootstrap_refuses_unsafe_app_or_migrator_roles(db_roles, unsafe_role, slot):
    from rootcause_db.bootstrap import bootstrap

    kwargs = {**db_roles.bootstrap_kwargs(), slot: unsafe_role}

    with pytest.raises(ConfigurationError):
        bootstrap(db_roles.admin_url, **kwargs)

    _no_grants_made(db_roles, unsafe_role)


PRIVILEGED_GROUPS = {
    "nologin-superuser-group": "CREATE ROLE {name} NOLOGIN SUPERUSER",
    "admin-superuser": None,
    "pg_execute_server_program": None,
    "pg_write_server_files": None,
    "pg_read_server_files": None,
}


@pytest.fixture
def privileged_group(request, db_roles):
    """A privileged role that a bootstrap role can be made a member of."""
    kind = request.param
    if kind == "admin-superuser":
        yield db_roles.admin_user
        return
    if kind.startswith("pg_"):
        yield kind
        return
    name = f"rc_group_{db_roles.owner[-10:]}"
    _admin_exec(db_roles, PRIVILEGED_GROUPS[kind].format(name=name))
    try:
        yield name
    finally:
        _admin_exec(db_roles, f"DROP ROLE IF EXISTS {name}")


@pytest.mark.parametrize("slot", ["app", "migrator", "owner"])
@pytest.mark.parametrize("privileged_group", list(PRIVILEGED_GROUPS), indirect=True)
def test_bootstrap_refuses_roles_that_are_members_of_privileged_roles(
    db_roles, privileged_group, slot
):
    from rootcause_db.bootstrap import bootstrap

    member = getattr(db_roles, slot)
    _admin_exec(db_roles, f'GRANT "{privileged_group}" TO "{member}"')

    with pytest.raises(ConfigurationError):
        bootstrap(db_roles.admin_url, **db_roles.bootstrap_kwargs())

    _no_grants_made(db_roles, db_roles.app)
    _no_grants_made(db_roles, db_roles.migrator)


@pytest.mark.parametrize("attribute", ["CREATEROLE", "CREATEDB"])
def test_bootstrap_refuses_an_app_role_that_can_create_roles_or_databases(db_roles, attribute):
    from rootcause_db.bootstrap import bootstrap

    _admin_exec(db_roles, f'ALTER ROLE "{db_roles.app}" {attribute}')

    with pytest.raises(ConfigurationError):
        bootstrap(db_roles.admin_url, **db_roles.bootstrap_kwargs())

    _no_grants_made(db_roles, db_roles.app)


def test_bootstrap_refuses_a_role_that_does_not_exist(db_roles):
    from rootcause_db.bootstrap import bootstrap

    kwargs = {**db_roles.bootstrap_kwargs(), "app_role": "rc_no_such_role_x"}

    with pytest.raises(ConfigurationError, match="rc_no_such_role_x"):
        bootstrap(db_roles.admin_url, **kwargs)


@pytest.mark.parametrize(
    "role_name",
    ['evil"; DROP SCHEMA audit CASCADE; --', "pct%s:colon'q"],
    ids=["injection", "special-chars"],
)
def test_bootstrap_treats_role_names_as_identifiers(db_roles, role_name):
    from rootcause_db.bootstrap import bootstrap

    kwargs = {**db_roles.bootstrap_kwargs(), "app_role": role_name}

    with pytest.raises(ConfigurationError):
        bootstrap(db_roles.admin_url, **kwargs)

    assert _scalars(db_roles.admin_url, "SELECT count(*) FROM pg_default_acl") == [0]
