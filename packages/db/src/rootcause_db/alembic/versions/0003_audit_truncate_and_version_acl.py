"""Close the remaining audit tamper paths: TRUNCATE and the version table.

- A statement-level ``BEFORE TRUNCATE`` trigger on ``audit.query_log`` rejects
  TRUNCATE for every role, including the owner and ``TRUNCATE ... CASCADE``
  from ``app.connections`` (row triggers from 0002 do not fire on TRUNCATE).
- ``app.alembic_version`` is created under the owner role, so bootstrap's
  default privileges would grant the app role DML on it. Every grantee other
  than the owner (the app role and PUBLIC included) loses all privileges, so
  the runtime role can neither read nor rewrite the migration state.

Downgrading past this revision removes the protection and is refused in prod.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rootcause_db.guards import refuse_in_prod

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = "audit.reject_query_log_mutation"  # created in 0002; raises "append-only"
TRUNCATE_TRIGGER = "query_log_no_truncate"
VERSION_TABLE = "app.alembic_version"
PUBLIC_GRANTEE_OID = 0


def upgrade() -> None:
    op.execute(
        f"CREATE TRIGGER {TRUNCATE_TRIGGER} BEFORE TRUNCATE ON audit.query_log "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()"
    )
    _revoke_version_table_grants()


def _revoke_version_table_grants() -> None:
    connection = op.get_bind()
    grantees = connection.execute(
        text(
            "SELECT DISTINCT a.grantee FROM pg_class c, aclexplode(c.relacl) a "
            "WHERE c.oid = CAST(:table AS regclass) AND a.grantee <> c.relowner"
        ),
        {"table": VERSION_TABLE},
    ).scalars()
    quote = connection.dialect.identifier_preparer.quote_identifier
    for oid in list(grantees):
        if oid == PUBLIC_GRANTEE_OID:
            grantee = "PUBLIC"
        else:
            name = connection.execute(
                text("SELECT rolname FROM pg_roles WHERE oid = :oid"), {"oid": oid}
            ).scalar_one()
            grantee = quote(name)
        # Quoted identifier; exec_driver_sql keeps ':' and '%' literal.
        connection.exec_driver_sql(f"REVOKE ALL ON {VERSION_TABLE} FROM {grantee}")


def downgrade() -> None:
    refuse_in_prod("Downgrading below revision 0003 (audit log TRUNCATE protection)")
    op.execute(f"DROP TRIGGER IF EXISTS {TRUNCATE_TRIGGER} ON audit.query_log")
