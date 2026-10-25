"""Initial schema: connections, audit query log and catalog versions.

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

query_status = postgresql.ENUM(
    "ok", "error", "rejected", name="query_status", schema="audit", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("dialect", sa.String(), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column(
            "limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("read_only_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connections")),
        schema="app",
    )
    query_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "query_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("agent", sa.String(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("status", query_status, nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["app.connections.id"],
            name=op.f("fk_query_log_connection_id_connections"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_log")),
        schema="audit",
    )
    op.create_index(
        "ix_query_log_connection_id_started_at",
        "query_log",
        ["connection_id", "started_at"],
        schema="audit",
    )
    op.create_table(
        "versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["app.connections.id"],
            name=op.f("fk_versions_connection_id_connections"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_versions")),
        sa.UniqueConstraint(
            "connection_id", "version", name=op.f("uq_versions_connection_id_version")
        ),
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_table("versions", schema="catalog")
    op.drop_index("ix_query_log_connection_id_started_at", table_name="query_log", schema="audit")
    op.drop_table("query_log", schema="audit")
    query_status.drop(op.get_bind(), checkfirst=True)
    op.drop_table("connections", schema="app")
