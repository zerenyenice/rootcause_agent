"""ORM models of the metadata catalog: versions, imported tables/columns and embeddings.

``catalog.tables`` and ``catalog.columns`` hold the metadata imported from a
connection, with the analyst's ``selected`` flag and description; nothing is
selected by default at the database level (fail closed). ``catalog.embeddings``
holds one pgvector embedding per indexed table or column. Deleting a connection
removes its catalog (``ON DELETE CASCADE`` down the chain). The embedding column
has no fixed dimension: the dimension is a setting checked by the indexer.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

# Registers app.connections on Base.metadata so the foreign keys below resolve
# when only the catalog models are imported.
import rootcause.connections.models  # noqa: F401
from rootcause.db.base import Base
from rootcause.db.schemas import APP_SCHEMA, CATALOG_SCHEMA

_CONNECTION_ID = f"{APP_SCHEMA}.connections.id"
_GENERATED_UUID = text("gen_random_uuid()")
_FALSE = text("false")


class CatalogVersion(Base):
    """A numbered catalog snapshot for one connection; unpublished while ``published_at`` is null.

    The foreign key targets ``app.connections`` (owned by ``rootcause.connections``).
    """

    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("connection_id", "version"),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(_CONNECTION_ID))
    version: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CatalogTable(Base):
    """A table (or view) of a connection, unique per (connection, schema, name)."""

    __tablename__ = "tables"
    __table_args__ = (
        UniqueConstraint("connection_id", "schema_name", "name"),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GENERATED_UUID
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(_CONNECTION_ID, ondelete="CASCADE")
    )
    schema_name: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    row_estimate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, server_default=_FALSE)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class CatalogColumn(Base):
    """A column of a catalog table, unique per (table, name); ``ordinal`` is 1-based."""

    __tablename__ = "columns"
    __table_args__ = (
        UniqueConstraint("table_id", "name"),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GENERATED_UUID
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{CATALOG_SCHEMA}.tables.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String)
    data_type: Mapped[str] = mapped_column(String)
    nullable: Mapped[bool] = mapped_column(Boolean)
    ordinal: Mapped[int] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(Boolean, server_default=_FALSE)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class CatalogEmbedding(Base):
    """One indexed entry: a table (``column_id`` NULL) or one of its columns."""

    __tablename__ = "embeddings"
    __table_args__ = (
        Index("ix_embeddings_connection_id", "connection_id"),
        Index("ix_embeddings_table_id", "table_id"),
        Index("ix_embeddings_column_id", "column_id"),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=_GENERATED_UUID
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(_CONNECTION_ID, ondelete="CASCADE")
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{CATALOG_SCHEMA}.tables.id", ondelete="CASCADE")
    )
    column_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{CATALOG_SCHEMA}.columns.id", ondelete="CASCADE"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
