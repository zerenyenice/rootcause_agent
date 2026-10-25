"""Storage of registered data source connections.

:class:`ConnectionRecord` is the immutable stored form; the secret stays encrypted
(``encrypted_secret``) and is ``None`` for file-based (DuckDB) connections.
:class:`SqlConnectionRepository` persists records in ``app.connections`` on the
application database; :class:`InMemoryConnectionRepository` keeps them in process
(tests, local experiments). Both implement :class:`ConnectionRepository`.
"""

import dataclasses
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from rootcause.connections.models import Connection
from rootcause.errors import RootCauseError

ConnectionKind = Literal["postgres", "duckdb"]

# Record field -> ``app.connections`` column, where the names differ.
_COLUMN_OF = {"name": "display_name", "kind": "dialect", "user": "username"}
VERIFIED_FIELD = "read_only_verified_at"
# Where and as whom a connection logs in; a probe result is only valid for these values.
LOGIN_FIELDS = ("host", "port", "database", "user", "sslmode", "path", "encrypted_secret")
UPDATABLE_FIELDS = frozenset(
    {
        "name",
        "host",
        "port",
        "database",
        "user",
        "sslmode",
        "path",
        "encrypted_secret",
        "read_only_verified_at",
    }
)


class ConnectionInUse(RootCauseError):
    """The connection is still referenced (for example by the append-only query audit log)."""

    code = "connection_in_use"
    status_code = 409
    public_message = "The connection is referenced by audit records and cannot be deleted."

    def __init__(self) -> None:
        super().__init__("Connection delete refused: rows still reference it.")


@dataclass(frozen=True, slots=True)
class NewConnection:
    """A connection to store; network fields are ``None`` for DuckDB, ``path`` for Postgres."""

    name: str
    kind: ConnectionKind
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    sslmode: str | None = None
    path: str | None = None
    encrypted_secret: bytes | None = None


@dataclass(frozen=True, slots=True)
class ConnectionRecord:
    """A stored connection. ``read_only_verified_at`` is set by a successful read-only probe."""

    id: uuid.UUID
    name: str
    kind: ConnectionKind
    host: str | None
    port: int | None
    database: str | None
    user: str | None
    sslmode: str | None
    path: str | None
    encrypted_secret: bytes | None
    read_only_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConnectionRepository(Protocol):
    """Async CRUD over stored connections."""

    async def create(self, new: NewConnection) -> ConnectionRecord: ...

    async def get(self, connection_id: uuid.UUID) -> ConnectionRecord | None: ...

    async def list(self) -> tuple[ConnectionRecord, ...]: ...

    async def update(
        self, connection_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> ConnectionRecord | None: ...

    async def delete(self, connection_id: uuid.UUID) -> bool: ...

    async def record_verification(
        self, probed: ConnectionRecord, verified_at: datetime | None
    ) -> bool:
        """Set ``read_only_verified_at`` only if the login (``LOGIN_FIELDS``) still equals
        ``probed``'s; returns whether it was recorded."""
        ...


def _touches(changes: Mapping[str, Any]) -> bool:
    """Whether ``changes`` edit the connection itself (a probe result alone does not)."""
    return bool(set(changes) - {VERIFIED_FIELD})


def _checked(changes: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"Not updatable: {', '.join(sorted(unknown))}")
    return dict(changes)


class InMemoryConnectionRepository:
    """Process-local repository; keeps creation order. Not shared between processes."""

    def __init__(self) -> None:
        self._records: dict[uuid.UUID, ConnectionRecord] = {}

    async def create(self, new: NewConnection) -> ConnectionRecord:
        now = datetime.now(UTC)
        record = ConnectionRecord(
            id=uuid.uuid4(),
            read_only_verified_at=None,
            created_at=now,
            updated_at=now,
            **{field.name: getattr(new, field.name) for field in dataclasses.fields(new)},
        )
        self._records = {**self._records, record.id: record}
        return record

    async def get(self, connection_id: uuid.UUID) -> ConnectionRecord | None:
        return self._records.get(connection_id)

    async def list(self) -> tuple[ConnectionRecord, ...]:
        return tuple(self._records.values())

    async def update(
        self, connection_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> ConnectionRecord | None:
        values = _checked(changes)
        current = self._records.get(connection_id)
        if current is None:
            return None
        updated_at = datetime.now(UTC) if _touches(values) else current.updated_at
        updated = dataclasses.replace(current, **values, updated_at=updated_at)
        self._records = {**self._records, connection_id: updated}
        return updated

    async def record_verification(
        self, probed: ConnectionRecord, verified_at: datetime | None
    ) -> bool:
        current = self._records.get(probed.id)
        if current is None or _login(current) != _login(probed):
            return False
        await self.update(probed.id, {VERIFIED_FIELD: verified_at})
        return True

    async def delete(self, connection_id: uuid.UUID) -> bool:
        if connection_id not in self._records:
            return False
        self._records = {key: rec for key, rec in self._records.items() if key != connection_id}
        return True


def _login(record: ConnectionRecord) -> tuple[Any, ...]:
    return tuple(getattr(record, name) for name in LOGIN_FIELDS)


def _columns(values: Mapping[str, Any]) -> dict[str, Any]:
    return {_COLUMN_OF.get(name, name): value for name, value in values.items()}


def _record(row: Connection) -> ConnectionRecord:
    kind: ConnectionKind = "duckdb" if row.dialect == "duckdb" else "postgres"
    return ConnectionRecord(
        id=row.id,
        name=row.display_name,
        kind=kind,
        host=row.host,
        port=row.port,
        database=row.database,
        user=row.username,
        sslmode=row.sslmode,
        path=row.path,
        encrypted_secret=None if row.encrypted_secret is None else bytes(row.encrypted_secret),
        read_only_verified_at=row.read_only_verified_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlConnectionRepository:
    """Repository on ``app.connections``; every call is one short transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def create(self, new: NewConnection) -> ConnectionRecord:
        values = _columns(dataclasses.asdict(new))
        async with self._sessions.begin() as session:
            row = await session.scalar(insert(Connection).values(**values).returning(Connection))
        if row is None:  # pragma: no cover - INSERT ... RETURNING always yields the row
            raise RuntimeError("INSERT returned no row")
        return _record(row)

    async def get(self, connection_id: uuid.UUID) -> ConnectionRecord | None:
        async with self._sessions() as session:
            row = await session.get(Connection, connection_id)
        return None if row is None else _record(row)

    async def list(self) -> tuple[ConnectionRecord, ...]:
        statement = select(Connection).order_by(Connection.created_at, Connection.id)
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return tuple(_record(row) for row in rows)

    async def update(
        self, connection_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> ConnectionRecord | None:
        # Keeping the old value also stops the ORM ``onupdate`` from bumping it.
        updated_at = datetime.now(UTC) if _touches(changes) else Connection.updated_at
        values = {**_columns(_checked(changes)), "updated_at": updated_at}
        statement = (
            update(Connection)
            .where(Connection.id == connection_id)
            .values(**values)
            .returning(Connection)
        )
        async with self._sessions.begin() as session:
            row = await session.scalar(statement)
        return None if row is None else _record(row)

    async def record_verification(
        self, probed: ConnectionRecord, verified_at: datetime | None
    ) -> bool:
        # One conditional UPDATE: a concurrent login change makes it match no row.
        unchanged = [
            getattr(Connection, _COLUMN_OF.get(name, name)).is_not_distinct_from(
                getattr(probed, name)
            )
            for name in LOGIN_FIELDS
        ]
        statement = (
            update(Connection)
            .where(Connection.id == probed.id, *unchanged)
            .values({VERIFIED_FIELD: verified_at, "updated_at": Connection.updated_at})
            .returning(Connection.id)
        )
        async with self._sessions.begin() as session:
            return await session.scalar(statement) is not None

    async def delete(self, connection_id: uuid.UUID) -> bool:
        statement = (
            delete(Connection).where(Connection.id == connection_id).returning(Connection.id)
        )
        try:
            async with self._sessions.begin() as session:
                return await session.scalar(statement) is not None
        except IntegrityError:
            pass
        # Raised outside the handler: the driver error quotes row values.
        raise ConnectionInUse()

    async def dispose(self) -> None:
        """Close the engine's pooled connections."""
        await self._engine.dispose()
