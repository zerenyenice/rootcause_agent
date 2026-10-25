"""Make audit.query_log append-only: a trigger rejects UPDATE and DELETE.

PROJECT.md section 7 requires an immutable query audit log. Downgrading past
this revision removes the protection and is refused in prod.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op

from rootcause.db.guards import refuse_in_prod

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = "audit.reject_query_log_mutation"
TRIGGER = "query_log_append_only"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit.query_log is append-only: % is not allowed', TG_OP;
        END;
        $$
        """
    )
    op.execute(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE OR DELETE ON audit.query_log "
        f"FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()"
    )


def downgrade() -> None:
    refuse_in_prod("Downgrading below revision 0002 (audit log immutability)")
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON audit.query_log")
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
