"""ORM models for data source connections and the query audit log."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from rootcause_core.db.base import Base
from rootcause_core.db.schemas import APP_SCHEMA, AUDIT_SCHEMA

QUERY_STATUSES = ("ok", "error", "rejected")


class Connection(Base):
    """A customer data source; the secret is stored encrypted."""

    __tablename__ = "connections"
    __table_args__ = {"schema": APP_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String)
    dialect: Mapped[str] = mapped_column(String)
    host: Mapped[str] = mapped_column(String)
    port: Mapped[int] = mapped_column(Integer)
    database: Mapped[str] = mapped_column(String)
    username: Mapped[str] = mapped_column(String)
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    read_only_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True)


class QueryAuditLog(Base):
    """Append-only record of every query executed against a customer database."""

    __tablename__ = "query_log"
    __table_args__ = (
        Index("ix_query_log_connection_id_started_at", "connection_id", "started_at"),
        {"schema": AUDIT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{APP_SCHEMA}.connections.id")
    )
    thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    agent: Mapped[str | None] = mapped_column(String, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    sql: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(*QUERY_STATUSES, name="query_status", schema=AUDIT_SCHEMA)
    )
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True)
