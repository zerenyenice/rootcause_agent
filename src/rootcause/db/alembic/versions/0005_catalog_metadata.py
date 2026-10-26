"""Metadata catalog: imported tables and columns, and their pgvector embeddings.

- ``catalog.tables``: one row per (connection, schema, table) with the analyst's
  ``selected`` flag and description;
- ``catalog.columns``: one row per (table, column);
- ``catalog.embeddings``: one vector per indexed table or column. ``vector`` has no
  fixed dimension here; the indexer checks it against ``embedding_dimensions``.

Every foreign key cascades, so deleting a connection removes its catalog. The
``vector`` extension is created by ``rootcause-db bootstrap`` (superuser), not here:
the migrator role cannot create extensions. Objects are created under ``SET LOCAL
ROLE`` to the owner role (``env.py``), so bootstrap's default privileges give the
app role DML on them.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catalog"
_UUID = postgresql.UUID(as_uuid=True)
_GENERATED_UUID = sa.text("gen_random_uuid()")


def _id() -> sa.Column[Any]:
    return sa.Column("id", _UUID, server_default=_GENERATED_UUID, nullable=False)


def upgrade() -> None:
    op.create_table(
        "tables",
        _id(),
        sa.Column("connection_id", _UUID, nullable=False),
        sa.Column("schema_name", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("row_estimate", sa.BigInteger(), nullable=True),
        sa.Column("selected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["app.connections.id"],
            name=op.f("fk_tables_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tables")),
        sa.UniqueConstraint(
            "connection_id",
            "schema_name",
            "name",
            name=op.f("uq_tables_connection_id_schema_name_name"),
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "columns",
        _id(),
        sa.Column("table_id", _UUID, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("data_type", sa.String(), nullable=False),
        sa.Column("nullable", sa.Boolean(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("selected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["catalog.tables.id"],
            name=op.f("fk_columns_table_id_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_columns")),
        sa.UniqueConstraint("table_id", "name", name=op.f("uq_columns_table_id_name")),
        schema=SCHEMA,
    )
    op.create_table(
        "embeddings",
        _id(),
        sa.Column("connection_id", _UUID, nullable=False),
        sa.Column("table_id", _UUID, nullable=False),
        sa.Column("column_id", _UUID, nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["app.connections.id"],
            name=op.f("fk_embeddings_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["catalog.tables.id"],
            name=op.f("fk_embeddings_table_id_tables"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["column_id"],
            ["catalog.columns.id"],
            name=op.f("fk_embeddings_column_id_columns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embeddings")),
        schema=SCHEMA,
    )
    for column in ("connection_id", "table_id", "column_id"):
        op.create_index(f"ix_embeddings_{column}", "embeddings", [column], schema=SCHEMA)


def downgrade() -> None:
    for column in ("column_id", "table_id", "connection_id"):
        op.drop_index(f"ix_embeddings_{column}", table_name="embeddings", schema=SCHEMA)
    op.drop_table("embeddings", schema=SCHEMA)
    op.drop_table("columns", schema=SCHEMA)
    op.drop_table("tables", schema=SCHEMA)
