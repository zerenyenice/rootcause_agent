"""Storage of the metadata catalog: imported tables/columns, selections and embeddings.

:class:`SqlCatalogRepository` (``catalog.tables``, ``catalog.columns``,
``catalog.embeddings`` on the application database) and
:class:`InMemoryCatalogRepository` (tests, local experiments) both implement
:class:`CatalogRepository`. Records are immutable; every write returns new records.
"""

import dataclasses
import math
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from rootcause.catalog.import_plan import ImportPlan, plan_import
from rootcause.catalog.records import (
    CatalogColumnRecord,
    CatalogCounts,
    CatalogTableRecord,
    EmbeddingEntry,
    SearchHit,
    checked_changes,
)
from rootcause.catalog.sql_repository import SqlCatalogRepository
from rootcause.connections.probe import Introspection

__all__ = [
    "CatalogRepository",
    "InMemoryCatalogRepository",
    "SqlCatalogRepository",
]


class CatalogRepository(Protocol):
    """Async access to one application's catalog, scoped by connection."""

    async def list_tables(self, connection_id: uuid.UUID) -> tuple[CatalogTableRecord, ...]:
        """Tables sorted by (schema, name), columns in declaration order."""
        ...

    async def update_table(
        self, table_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> CatalogTableRecord | None: ...

    async def update_column(
        self, column_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> CatalogColumnRecord | None: ...

    async def apply_import(
        self, connection_id: uuid.UUID, introspection: Introspection
    ) -> CatalogCounts | None:
        """Upsert ``introspection`` (see ``import_plan``); the totals now stored, or ``None``
        when the connection no longer exists."""
        ...

    async def replace_embeddings(
        self, connection_id: uuid.UUID, entries: Sequence[EmbeddingEntry]
    ) -> None:
        """Atomically replace the connection's index with ``entries``."""
        ...

    async def index_counts(self, connection_id: uuid.UUID) -> CatalogCounts:
        """Stored table and column entries of the connection's index."""
        ...

    async def search(
        self, connection_id: uuid.UUID, vector: Sequence[float], k: int
    ) -> tuple[SearchHit, ...]:
        """The ``k`` stored entries most similar to ``vector`` (cosine), restricted to
        tables and columns that are still selected."""
        ...


def _sort_key(table: CatalogTableRecord) -> tuple[str, str]:
    return table.schema, table.name


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Cosine similarity, or ``None`` for vectors of different length or zero norm."""
    if len(left) != len(right):
        return None
    norm = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(y * y for y in right))
    if norm == 0:
        return None
    return sum(x * y for x, y in zip(left, right, strict=True)) / norm


class InMemoryCatalogRepository:
    """Process-local catalog with the same cascades as the database. Not shared."""

    def __init__(self) -> None:
        self._tables: dict[uuid.UUID, CatalogTableRecord] = {}  # stored without columns
        self._columns: dict[uuid.UUID, CatalogColumnRecord] = {}
        self._embeddings: dict[uuid.UUID, tuple[EmbeddingEntry, ...]] = {}

    def _assembled(self, table: CatalogTableRecord) -> CatalogTableRecord:
        columns = sorted(
            (c for c in self._columns.values() if c.table_id == table.id), key=lambda c: c.ordinal
        )
        return dataclasses.replace(table, columns=tuple(columns))

    async def list_tables(self, connection_id: uuid.UUID) -> tuple[CatalogTableRecord, ...]:
        tables = (t for t in self._tables.values() if t.connection_id == connection_id)
        return tuple(self._assembled(t) for t in sorted(tables, key=_sort_key))

    async def update_table(
        self, table_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> CatalogTableRecord | None:
        values = checked_changes(changes)
        current = self._tables.get(table_id)
        if current is None:
            return None
        self._tables = {**self._tables, table_id: dataclasses.replace(current, **values)}
        return self._assembled(self._tables[table_id])

    async def update_column(
        self, column_id: uuid.UUID, changes: Mapping[str, Any]
    ) -> CatalogColumnRecord | None:
        values = checked_changes(changes)
        current = self._columns.get(column_id)
        if current is None:
            return None
        updated = dataclasses.replace(current, **values)
        self._columns = {**self._columns, column_id: updated}
        return updated

    async def apply_import(
        self, connection_id: uuid.UUID, introspection: Introspection
    ) -> CatalogCounts:
        plan = plan_import(await self.list_tables(connection_id), introspection)
        self._apply(connection_id, plan)
        tables = await self.list_tables(connection_id)
        return CatalogCounts(len(tables), sum(len(t.columns) for t in tables))

    def _apply(self, connection_id: uuid.UUID, plan: ImportPlan) -> None:
        tables = {k: v for k, v in self._tables.items() if k not in set(plan.deleted_tables)}
        for row in plan.updated_tables:
            tables[row.id] = dataclasses.replace(tables[row.id], row_estimate=row.row_estimate)
        for row in plan.new_tables:
            tables[row.id] = CatalogTableRecord(
                id=row.id,
                connection_id=connection_id,
                schema=row.schema,
                name=row.name,
                row_estimate=row.row_estimate,
                selected=False,
                description=None,
                columns=(),
            )
        gone_columns = set(plan.deleted_columns)
        columns = {
            k: v for k, v in self._columns.items() if k not in gone_columns and v.table_id in tables
        }
        for col in plan.updated_columns:
            columns[col.id] = dataclasses.replace(
                columns[col.id], data_type=col.data_type, nullable=col.nullable, ordinal=col.ordinal
            )
        for col in plan.new_columns:
            columns[col.id] = CatalogColumnRecord(
                id=col.id,
                table_id=col.table_id,
                name=col.name,
                data_type=col.data_type,
                nullable=col.nullable,
                ordinal=col.ordinal,
                selected=col.selected,
                description=None,
            )
        self._tables, self._columns = tables, columns
        self._embeddings = {
            key: tuple(e for e in entries if self._is_stored(e))
            for key, entries in self._embeddings.items()
        }

    def _is_stored(self, entry: EmbeddingEntry) -> bool:
        """Whether the entry's table (and column) still exist (the cascade)."""
        if entry.table_id not in self._tables:
            return False
        return entry.column_id is None or entry.column_id in self._columns

    async def replace_embeddings(
        self, connection_id: uuid.UUID, entries: Sequence[EmbeddingEntry]
    ) -> None:
        if not all(self._is_stored(entry) for entry in entries):
            raise LookupError("an entry references a table or column that no longer exists")
        self._embeddings = {**self._embeddings, connection_id: tuple(entries)}

    async def index_counts(self, connection_id: uuid.UUID) -> CatalogCounts:
        entries = self._embeddings.get(connection_id, ())
        columns = sum(1 for e in entries if e.column_id is not None)
        return CatalogCounts(tables=len(entries) - columns, columns=columns)

    async def search(
        self, connection_id: uuid.UUID, vector: Sequence[float], k: int
    ) -> tuple[SearchHit, ...]:
        scored: list[SearchHit] = []
        for entry in self._embeddings.get(connection_id, ()):
            hit = self._hit(entry, vector)
            if hit is not None:
                scored.append(hit)
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return tuple(scored[: max(k, 0)])

    def _hit(self, entry: EmbeddingEntry, vector: Sequence[float]) -> SearchHit | None:
        table = self._tables.get(entry.table_id)
        column = self._columns.get(entry.column_id) if entry.column_id is not None else None
        if table is None or not table.selected:
            return None
        if entry.column_id is not None and (column is None or not column.selected):
            return None
        score = cosine_similarity(entry.vector, vector)
        if score is None:
            return None
        return SearchHit(
            kind="table" if column is None else "column",
            schema=table.schema,
            table=table.name,
            column=None if column is None else column.name,
            description=table.description if column is None else column.description,
            score=score,
        )
