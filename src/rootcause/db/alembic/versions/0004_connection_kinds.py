"""Connections of several kinds: file paths and sslmode, optional network fields.

- ``path`` holds the file of a file-based (DuckDB) connection; ``sslmode`` the
  libpq SSL mode of a Postgres connection.
- ``host``, ``port``, ``database``, ``username`` and ``encrypted_secret`` become
  nullable, since a DuckDB connection has none of them. The ``port_range`` check
  already passes for NULL (a CHECK is only violated when it evaluates to false).

Only columns change: no object is created, so ownership and grants from
bootstrap and 0001-0003 are untouched. Downgrading fails (NOT NULL violation)
while any connection lacks a network field, i.e. while DuckDB connections exist.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "app"
TABLE = "connections"
_OPTIONAL_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any]], ...] = (
    ("host", sa.String()),
    ("port", sa.Integer()),
    ("database", sa.String()),
    ("username", sa.String()),
    ("encrypted_secret", sa.LargeBinary()),
)


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("sslmode", sa.String(), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("path", sa.String(), nullable=True), schema=SCHEMA)
    for name, type_ in _OPTIONAL_COLUMNS:
        op.alter_column(TABLE, name, existing_type=type_, nullable=True, schema=SCHEMA)


def downgrade() -> None:
    for name, type_ in _OPTIONAL_COLUMNS:
        op.alter_column(TABLE, name, existing_type=type_, nullable=False, schema=SCHEMA)
    op.drop_column(TABLE, "path", schema=SCHEMA)
    op.drop_column(TABLE, "sslmode", schema=SCHEMA)
