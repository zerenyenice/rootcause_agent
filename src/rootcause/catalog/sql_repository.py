"""Catalog repository on ``catalog.tables``/``catalog.columns``/``catalog.embeddings``.

Imports and index replacements of one connection are serialised by locking its
``app.connections`` row (``SELECT ... FOR UPDATE``), so concurrent imports neither
fail on the unique constraints nor duplicate rows, and a connection deleted
meanwhile is detected. Vectors travel as pgvector's text form (``[1.0,2.0]``) and
are cast in SQL, so no driver-level vector codec is needed.
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from typing import cast as typed

from sqlalchemy import (
    Table,
    Text,
    bindparam,
    cast,
    delete,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from rootcause.catalog.import_plan import ImportPlan, plan_import
from rootcause.catalog.models import CatalogColumn, CatalogEmbedding, CatalogTable
from rootcause.catalog.records import (
    CatalogColumnRecord,
    CatalogCounts,
    CatalogTableRecord,
    EmbeddingEntry,
    SearchHit,
    checked_changes,
)
from rootcause.connections.models import Connection
from rootcause.connections.probe import Introspection

_TABLES = typed(Table, CatalogTable.__table__)
_COLUMNS = typed(Table, CatalogColumn.__table__)
_EMBEDDINGS = typed(Table, CatalogEmbedding.__table__)

# Cosine distance (``<=>``) to the query vector, restricted to still-selected entries.
_SEARCH_SQL = text(
    "SELECT e.column_id IS NOT NULL AS is_column, t.schema_name, t.name, c.name, "
    "CASE WHEN e.column_id IS NULL THEN t.description ELSE c.description END, "
    "1 - (e.embedding <=> CAST(CAST(:query AS text) AS vector)) AS score "
    "FROM catalog.embeddings AS e "
    "JOIN catalog.tables AS t ON t.id = e.table_id "
    "LEFT JOIN catalog.columns AS c ON c.id = e.column_id "
    "WHERE e.connection_id = :connection_id AND t.selected "
    "AND (e.column_id IS NULL OR c.selected) "
    "ORDER BY e.embedding <=> CAST(CAST(:query AS text) AS vector) "
    "LIMIT :k"
)


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text form of ``vector``."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def _column(row: Any) -> CatalogColumnRecord:
    return CatalogColumnRecord(
        id=row.id,
        table_id=row.table_id,
        name=row.name,
        data_type=row.data_type,
        nullable=row.nullable,
        ordinal=row.ordinal,
        selected=row.selected,
        description=row.description,
    )


def _table(row: Any, columns: tuple[CatalogColumnRecord, ...]) -> CatalogTableRecord:
    return CatalogTableRecord(
        id=row.id,
        connection_id=row.connection_id,
        schema=row.schema_name,
        name=row.name,
        row_estimate=row.row_estimate,
        selected=row.selected,
        description=row.description,
        columns=columns,
    )


async def _tables_where(connection: AsyncConnection, *where: Any) -> tuple[CatalogTableRecord, ...]:
    tables = (
        await connection.execute(
            select(_TABLES).where(*where).order_by(_TABLES.c.schema_name, _TABLES.c.name)
        )
    ).all()
    if not tables:
        return ()
    ids = [row.id for row in tables]
    rows = (
        await connection.execute(
            select(_COLUMNS)
            .where(_COLUMNS.c.table_id.in_(ids))
            .order_by(_COLUMNS.c.table_id, _COLUMNS.c.ordinal)
        )
    ).all()
    by_table: dict[uuid.UUID, list[CatalogColumnRecord]] = {}
    for row in rows:
        by_table.setdefault(row.table_id, []).append(_column(row))
    return tuple(_table(row, tuple(by_table.get(row.id, ()))) for row in tables)


async def _lock_connection(connection: AsyncConnection, connection_id: uuid.UUID) -> bool:
    """Lock the connection row until commit; ``False`` when it does not exist."""
    statement = select(Connection.id).where(Connection.id == connection_id).with_for_update()
    return (await connection.execute(statement)).first() is not None


async def _apply(connection: AsyncConnection, connection_id: uuid.UUID, plan: ImportPlan) -> None:
    if plan.deleted_tables:
        await connection.execute(delete(_TABLES).where(_TABLES.c.id.in_(plan.deleted_tables)))
    if plan.deleted_columns:
        await connection.execute(delete(_COLUMNS).where(_COLUMNS.c.id.in_(plan.deleted_columns)))
    if plan.updated_tables:
        await connection.execute(
            update(_TABLES)
            .where(_TABLES.c.id == bindparam("table_id"))
            .values(row_estimate=bindparam("estimate")),
            [{"table_id": t.id, "estimate": t.row_estimate} for t in plan.updated_tables],
        )
    if plan.new_tables:
        await connection.execute(
            insert(_TABLES),
            [
                {
                    "id": t.id,
                    "connection_id": connection_id,
                    "schema_name": t.schema,
                    "name": t.name,
                    "row_estimate": t.row_estimate,
                    "selected": False,
                }
                for t in plan.new_tables
            ],
        )
    if plan.updated_columns:
        await connection.execute(
            update(_COLUMNS)
            .where(_COLUMNS.c.id == bindparam("column_id"))
            .values(
                data_type=bindparam("new_type"),
                nullable=bindparam("new_nullable"),
                ordinal=bindparam("new_ordinal"),
            ),
            [
                {
                    "column_id": c.id,
                    "new_type": c.data_type,
                    "new_nullable": c.nullable,
                    "new_ordinal": c.ordinal,
                }
                for c in plan.updated_columns
            ],
        )
    if plan.new_columns:
        await connection.execute(
            insert(_COLUMNS),
            [
                {
                    "id": c.id,
                    "table_id": c.table_id,
                    "name": c.name,
                    "data_type": c.data_type,
                    "nullable": c.nullable,
                    "ordinal": c.ordinal,
                    "selected": c.selected,
                }
                for c in plan.new_columns
            ],
        )


async def _counts(connection: AsyncConnection, connection_id: uuid.UUID) -> CatalogCounts:
    tables = await connection.scalar(
        select(func.count()).select_from(_TABLES).where(_TABLES.c.connection_id == connection_id)
    )
    columns = await connection.scalar(
        select(func.count())
        .select_from(_COLUMNS.join(_TABLES, _COLUMNS.c.table_id == _TABLES.c.id))
        .where(_TABLES.c.connection_id == connection_id)
    )
    return CatalogCounts(tables=int(tables or 0), columns=int(columns or 0))


class SqlCatalogRepository:
    """Catalog repository on the application database; every call is one transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list_tables(self, connection_id: uuid.UUID) -> tuple[CatalogTableRecord, ...]:
        async with self._engine.connect() as connection:
            return await _tables_where(connection, _TABLES.c.connection_id == connection_id)

    async def update_table(
        self, table_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> CatalogTableRecord | None:
        values = checked_changes(changes)
        async with self._engine.begin() as connection:
            if values:
                await connection.execute(
                    update(_TABLES).where(_TABLES.c.id == table_id).values(**values)
                )
            found = await _tables_where(connection, _TABLES.c.id == table_id)
        return found[0] if found else None

    async def update_column(
        self, column_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> CatalogColumnRecord | None:
        values = checked_changes(changes)
        async with self._engine.begin() as connection:
            if values:
                await connection.execute(
                    update(_COLUMNS).where(_COLUMNS.c.id == column_id).values(**values)
                )
            row = (
                await connection.execute(select(_COLUMNS).where(_COLUMNS.c.id == column_id))
            ).first()
        return None if row is None else _column(row)

    async def apply_import(
        self, connection_id: uuid.UUID, introspection: Introspection
    ) -> CatalogCounts | None:
        async with self._engine.begin() as connection:
            if not await _lock_connection(connection, connection_id):
                return None
            existing = await _tables_where(connection, _TABLES.c.connection_id == connection_id)
            await _apply(connection, connection_id, plan_import(existing, introspection))
            return await _counts(connection, connection_id)

    async def replace_embeddings(
        self, connection_id: uuid.UUID, entries: Sequence[EmbeddingEntry]
    ) -> None:
        async with self._engine.begin() as connection:
            if not await _lock_connection(connection, connection_id):
                raise LookupError("the connection no longer exists")
            await connection.execute(
                delete(_EMBEDDINGS).where(_EMBEDDINGS.c.connection_id == connection_id)
            )
            if not entries:
                return
            statement = insert(_EMBEDDINGS).values(
                embedding=cast(bindparam("vector_text", type_=Text), _EMBEDDINGS.c.embedding.type)
            )
            await connection.execute(
                statement,
                [
                    {
                        "connection_id": connection_id,
                        "table_id": entry.table_id,
                        "column_id": entry.column_id,
                        "content": entry.content,
                        "vector_text": vector_literal(entry.vector),
                    }
                    for entry in entries
                ],
            )

    async def index_counts(self, connection_id: uuid.UUID) -> CatalogCounts:
        statement = select(
            func.count().filter(_EMBEDDINGS.c.column_id.is_(None)),
            func.count().filter(_EMBEDDINGS.c.column_id.is_not(None)),
        ).where(_EMBEDDINGS.c.connection_id == connection_id)
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).one()
        return CatalogCounts(tables=int(row[0]), columns=int(row[1]))

    async def search(
        self, connection_id: uuid.UUID, vector: Sequence[float], k: int
    ) -> tuple[SearchHit, ...]:
        params = {"query": vector_literal(vector), "connection_id": connection_id, "k": k}
        async with self._engine.connect() as connection:
            rows = (await connection.execute(_SEARCH_SQL, params)).all()
        return tuple(
            SearchHit(
                kind="column" if is_column else "table",
                schema=schema,
                table=table,
                column=column,
                description=description,
                score=float(score),
            )
            for is_column, schema, table, column, description, score in rows
        )

    async def dispose(self) -> None:
        """Close the engine's pooled connections."""
        await self._engine.dispose()
