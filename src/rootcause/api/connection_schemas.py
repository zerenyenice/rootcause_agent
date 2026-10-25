"""Request and response bodies of ``/api/v1/connections``.

Create bodies are a union discriminated by ``kind``; unknown fields are rejected so a
field of the other kind (``path`` on a Postgres body, ``host`` on a DuckDB body) or a
server-managed one (``id``, ``encrypted_secret``) never slips through. Validation
messages never quote the submitted value (paths and passwords stay out of errors).
Responses carry no secret: the password is write-only.
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, field_validator

from rootcause.connections.models import MAX_PORT, MIN_PORT
from rootcause.connections.probe import ProbeOutcome, SchemaInfo
from rootcause.connections.repository import ConnectionKind, ConnectionRecord

DEFAULT_POSTGRES_PORT = 5432
DUCKDB_SUFFIX = ".duckdb"
MAX_NAME_CHARS = 100
MAX_FIELD_CHARS = 255
MAX_PASSWORD_CHARS = 1024
MAX_PATH_CHARS = 4096
_NO_NUL = r"^[^\x00]*$"

SslMode = Literal["disable", "prefer", "require", "verify-full"]
Name = Annotated[str, Field(min_length=1, max_length=MAX_NAME_CHARS, pattern=_NO_NUL)]
NetworkField = Annotated[str, Field(min_length=1, max_length=MAX_FIELD_CHARS, pattern=_NO_NUL)]
Port = Annotated[int, Field(ge=MIN_PORT, le=MAX_PORT)]
Password = Annotated[SecretStr, Field(min_length=1, max_length=MAX_PASSWORD_CHARS)]


def _duckdb_path(value: str) -> str:
    """An absolute path to an existing regular ``.duckdb`` file (the value is never echoed)."""
    if not value or "\x00" in value or len(value) > MAX_PATH_CHARS:
        raise ValueError("path must be a non-empty file path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    if path.suffix != DUCKDB_SUFFIX:
        raise ValueError(f"path must name a {DUCKDB_SUFFIX} file")
    if not path.is_file():
        raise ValueError("path must point at an existing regular file")
    return value


DuckDBPath = Annotated[str, AfterValidator(_duckdb_path)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PostgresCreate(_Strict):
    name: Name
    kind: Literal["postgres"]
    host: NetworkField
    port: Port = DEFAULT_POSTGRES_PORT
    database: NetworkField
    user: NetworkField
    password: Password
    sslmode: SslMode = "prefer"


class DuckDBCreate(_Strict):
    name: Name
    kind: Literal["duckdb"]
    path: DuckDBPath


class _Patch(_Strict):
    """Partial update: absent fields stay unchanged; an explicit ``null`` is rejected."""

    @field_validator("*", mode="before")
    @classmethod
    def _reject_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("may not be null")
        return value


class PostgresPatch(_Patch):
    name: Name | None = None
    host: NetworkField | None = None
    port: Port | None = None
    database: NetworkField | None = None
    user: NetworkField | None = None
    password: Password | None = None
    sslmode: SslMode | None = None


class DuckDBPatch(_Patch):
    name: Name | None = None
    path: DuckDBPath | None = None


class ConnectionOut(BaseModel):
    """Public view of a connection: never the password or its ciphertext."""

    id: uuid.UUID
    name: str
    kind: ConnectionKind
    host: str | None
    port: int | None
    database: str | None
    user: str | None
    sslmode: str | None
    path: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, record: ConnectionRecord) -> "ConnectionOut":
        return cls(
            id=record.id,
            name=record.name,
            kind=record.kind,
            host=record.host,
            port=record.port,
            database=record.database,
            user=record.user,
            sslmode=record.sslmode,
            path=record.path,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class DeletedOut(BaseModel):
    id: uuid.UUID
    deleted: bool


class ProbeOut(BaseModel):
    reachable: bool
    read_only: bool
    server_version: str | None
    error: str | None

    @classmethod
    def of(cls, outcome: ProbeOutcome) -> "ProbeOut":
        return cls(
            reachable=outcome.reachable,
            read_only=outcome.read_only,
            server_version=outcome.server_version,
            error=outcome.error,
        )


class ColumnOut(BaseModel):
    name: str
    type: str
    nullable: bool


class TableOut(BaseModel):
    name: str
    row_estimate: int
    columns: list[ColumnOut]


class SchemaOut(BaseModel):
    name: str
    tables: list[TableOut]


class IntrospectionOut(BaseModel):
    schemas: list[SchemaOut]

    @classmethod
    def of(cls, schemas: tuple[SchemaInfo, ...]) -> "IntrospectionOut":
        return cls(
            schemas=[
                SchemaOut(
                    name=schema.name,
                    tables=[
                        TableOut(
                            name=table.name,
                            row_estimate=table.row_estimate,
                            columns=[
                                ColumnOut(name=c.name, type=c.data_type, nullable=c.nullable)
                                for c in table.columns
                            ],
                        )
                        for table in schema.tables
                    ],
                )
                for schema in schemas
            ]
        )
