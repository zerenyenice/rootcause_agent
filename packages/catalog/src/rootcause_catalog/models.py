"""ORM model for catalog versions."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

# Registers app.connections on Base.metadata so the foreign key below resolves
# when only the catalog models are imported.
import rootcause_connections.models  # noqa: F401
from rootcause_core.db.base import Base
from rootcause_core.db.schemas import APP_SCHEMA, CATALOG_SCHEMA


class CatalogVersion(Base):
    """A numbered catalog snapshot for one connection; unpublished while ``published_at`` is null.

    The foreign key targets ``app.connections`` (owned by ``rootcause_connections``).
    """

    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("connection_id", "version"),
        {"schema": CATALOG_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{APP_SCHEMA}.connections.id")
    )
    version: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
