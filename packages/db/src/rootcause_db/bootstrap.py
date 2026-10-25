"""Superuser-only database preparation for the three-role model (docs/adr/0006).

Roles (created beforehand, e.g. by ``deploy/postgres/init.sql``):

- owner (NOLOGIN) owns the schemas and, via ``SET ROLE`` in ``env.py``, every
  object the migrations create;
- migrator (LOGIN, member of owner) runs the migrations;
- app (LOGIN) is the runtime role: ``USAGE`` on the schemas, DML on app and
  catalog tables, ``SELECT, INSERT`` on audit tables, nothing else.

``bootstrap`` is idempotent. Every role check runs before the first statement
that changes anything, and everything runs in one transaction.
"""

from typing import NamedTuple

from sqlalchemy import Connection, text

from rootcause_core.db.schemas import ALL_SCHEMAS, APP_SCHEMA, AUDIT_SCHEMA, CATALOG_SCHEMA
from rootcause_core.errors import ConfigurationError
from rootcause_db.migrate import make_migration_engine
from rootcause_db.names import ADMIN_URL_VARIABLE

VECTOR_EXTENSION = "vector"
RESERVED_ROLE_PREFIX = "pg_"
# Predefined roles that would let the runtime role bypass the audit grants.
FORBIDDEN_APP_MEMBERSHIPS = ("pg_write_all_data",)
TABLE_PRIVILEGES_BY_SCHEMA = {
    APP_SCHEMA: "SELECT, INSERT, UPDATE, DELETE",
    CATALOG_SCHEMA: "SELECT, INSERT, UPDATE, DELETE",
    AUDIT_SCHEMA: "SELECT, INSERT",  # append-only audit log (PROJECT.md section 7)
}
SEQUENCE_PRIVILEGES = "USAGE, SELECT"


class DbRoles(NamedTuple):
    """Names of the owner, migrator and app roles."""

    owner: str
    migrator: str
    app: str


class _RoleInfo(NamedTuple):
    is_superuser: bool
    bypasses_rls: bool
    can_login: bool


def bootstrap(admin_url: str, *, owner_role: str, migrator_role: str, app_role: str) -> None:
    """Prepare the database: extension, owner-owned schemas and least-privilege grants.

    Refuses (before any change) roles that are missing, reserved (``pg_*``),
    superusers, ``BYPASSRLS``, the admin user itself, an owner that can log in,
    an app role that is a member of the owner or migrator role, and an app or
    migrator role that owns the database.

    Raises:
        ConfigurationError: when ``admin_url`` is invalid or a role is refused.
        sqlalchemy.exc.SQLAlchemyError: when the database rejects a statement.
    """
    roles = DbRoles(owner=owner_role, migrator=migrator_role, app=app_role)
    engine = make_migration_engine(admin_url, source=ADMIN_URL_VARIABLE)
    try:
        with engine.begin() as connection:
            _verify_roles(connection, roles)
            _prepare(connection, roles)
    finally:
        engine.dispose()


# --- checks (read-only) -----------------------------------------------------


def _refuse(message: str) -> ConfigurationError:
    return ConfigurationError(f"bootstrap refused: {message}")


def _verify_roles(connection: Connection, roles: DbRoles) -> None:
    if len(set(roles)) != len(roles):
        raise _refuse("the owner, migrator and app roles must be three different roles.")
    infos = _role_infos(connection, roles)
    admin = connection.execute(text("SELECT session_user")).scalar_one()
    for slot, name in roles._asdict().items():
        _verify_role(slot, name, infos.get(name), admin)
    if infos[roles.owner].can_login:
        raise _refuse(f"owner role {roles.owner!r} must be NOLOGIN.")
    _verify_app_memberships(connection, roles)
    _verify_database_owner(connection, roles)


def _role_infos(connection: Connection, roles: DbRoles) -> dict[str, _RoleInfo]:
    rows = connection.execute(
        text(
            "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
            "WHERE rolname = ANY(:names)"
        ),
        {"names": list(roles)},
    )
    return {name: _RoleInfo(*flags) for name, *flags in rows}


def _verify_role(slot: str, name: str, info: _RoleInfo | None, admin: str) -> None:
    if name.startswith(RESERVED_ROLE_PREFIX):
        raise _refuse(f"{slot} role {name!r} is a reserved pg_* role.")
    if info is None:
        raise _refuse(f"{slot} role {name!r} does not exist; create it before bootstrap.")
    if name == admin:
        raise _refuse(f"{slot} role {name!r} is the admin user running bootstrap.")
    if info.is_superuser:
        raise _refuse(f"{slot} role {name!r} is a superuser.")
    if info.bypasses_rls:
        raise _refuse(f"{slot} role {name!r} has BYPASSRLS.")


def _verify_app_memberships(connection: Connection, roles: DbRoles) -> None:
    for privileged in (roles.owner, roles.migrator, *FORBIDDEN_APP_MEMBERSHIPS):
        if _is_member(connection, roles.app, privileged):
            raise _refuse(f"app role {roles.app!r} must not be a member of {privileged!r}.")


def _verify_database_owner(connection: Connection, roles: DbRoles) -> None:
    owner = connection.execute(
        text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
    ).scalar_one()
    if owner in (roles.app, roles.migrator):
        raise _refuse(f"role {owner!r} owns the database; transfer ownership to the admin user.")


def _is_member(connection: Connection, member: str, role: str) -> bool:
    return bool(
        connection.execute(
            text("SELECT pg_has_role(:member, :role, 'MEMBER')"), {"member": member, "role": role}
        ).scalar_one()
    )


# --- changes ----------------------------------------------------------------


def _prepare(connection: Connection, roles: DbRoles) -> None:
    quote = connection.dialect.identifier_preparer.quote_identifier
    owner, migrator, app = (quote(name) for name in roles)
    statements = [f"CREATE EXTENSION IF NOT EXISTS {VECTOR_EXTENSION}"]
    if not _is_member(connection, roles.migrator, roles.owner):
        statements.append(f"GRANT {owner} TO {migrator}")
    for schema in ALL_SCHEMAS:
        target = quote(schema)
        statements += [
            f"CREATE SCHEMA IF NOT EXISTS {target} AUTHORIZATION {owner}",
            f"ALTER SCHEMA {target} OWNER TO {owner}",
            f"REVOKE CREATE ON SCHEMA {target} FROM {app}",
            f"GRANT USAGE ON SCHEMA {target} TO {app}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {target} "
            f"GRANT {TABLE_PRIVILEGES_BY_SCHEMA[schema]} ON TABLES TO {app}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {target} "
            f"GRANT {SEQUENCE_PRIVILEGES} ON SEQUENCES TO {app}",
        ]
    statements.append("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    for statement in statements:
        # Identifiers are quoted and escaped; nothing else is interpolated.
        # exec_driver_sql: no bind-parameter parsing, so a ':' or '%' in a
        # quoted role name stays literal.
        connection.exec_driver_sql(statement)
