"""Pure planning of a catalog import: what to insert, update and delete.

Upsert by (schema, table) and (table, column): ids, ``selected`` and descriptions of
known entries survive. A newly discovered table is unselected and its columns are
selected (the table is the gate); a column that appears on an already-known table is
unselected, so nothing new becomes visible to the agent without a human action.
Entries gone from the source are deleted, unless the introspection was truncated:
then nothing is deleted, since an unseen table may simply have been cut off.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from rootcause.catalog.records import CatalogColumnRecord, CatalogTableRecord
from rootcause.connections.probe import Introspection, TableSchema


@dataclass(frozen=True, slots=True)
class TableRow:
    """A table to insert, or the refreshed source metadata of a known table."""

    id: uuid.UUID
    schema: str
    name: str
    row_estimate: int


@dataclass(frozen=True, slots=True)
class ColumnRow:
    """A column to insert (with ``selected``), or the refreshed metadata of a known one."""

    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    data_type: str
    nullable: bool
    ordinal: int
    selected: bool


@dataclass(frozen=True, slots=True)
class ImportPlan:
    new_tables: tuple[TableRow, ...]
    updated_tables: tuple[TableRow, ...]
    new_columns: tuple[ColumnRow, ...]
    updated_columns: tuple[ColumnRow, ...]
    deleted_tables: tuple[uuid.UUID, ...]
    deleted_columns: tuple[uuid.UUID, ...]


def _source_tables(introspection: Introspection) -> Iterable[tuple[str, TableSchema]]:
    for schema in introspection.schemas:
        for table in schema.tables:
            yield schema.name, table


def _columns(
    table_id: uuid.UUID,
    source: TableSchema,
    known: dict[str, CatalogColumnRecord],
    *,
    new_selected: bool,
) -> tuple[list[ColumnRow], list[ColumnRow]]:
    """(new, updated) column rows of one source table."""
    new: list[ColumnRow] = []
    updated: list[ColumnRow] = []
    for ordinal, column in enumerate(source.columns, start=1):
        existing = known.get(column.name)
        row = ColumnRow(
            id=existing.id if existing is not None else uuid.uuid4(),
            table_id=table_id,
            name=column.name,
            data_type=column.data_type,
            nullable=column.nullable,
            ordinal=ordinal,
            selected=existing.selected if existing is not None else new_selected,
        )
        (updated if existing is not None else new).append(row)
    return new, updated


def plan_import(existing: Iterable[CatalogTableRecord], introspection: Introspection) -> ImportPlan:
    """What applying ``introspection`` to the ``existing`` catalog changes."""
    known = {(table.schema, table.name): table for table in existing}
    new_tables: list[TableRow] = []
    updated_tables: list[TableRow] = []
    new_columns: list[ColumnRow] = []
    updated_columns: list[ColumnRow] = []
    seen_tables: set[uuid.UUID] = set()
    seen_columns: set[uuid.UUID] = set()
    for schema, source in _source_tables(introspection):
        table = known.get((schema, source.name))
        table_id = table.id if table is not None else uuid.uuid4()
        row = TableRow(table_id, schema, source.name, source.row_estimate)
        (updated_tables if table is not None else new_tables).append(row)
        columns = {c.name: c for c in table.columns} if table is not None else {}
        new, updated = _columns(table_id, source, columns, new_selected=table is None)
        new_columns.extend(new)
        updated_columns.extend(updated)
        seen_tables.add(table_id)
        seen_columns.update(column.id for column in updated)
    deleted_tables: tuple[uuid.UUID, ...] = ()
    deleted_columns: tuple[uuid.UUID, ...] = ()
    if not introspection.truncated:
        deleted_tables = tuple(t.id for t in known.values() if t.id not in seen_tables)
        deleted_columns = tuple(
            c.id
            for t in known.values()
            if t.id in seen_tables
            for c in t.columns
            if c.id not in seen_columns
        )
    return ImportPlan(
        new_tables=tuple(new_tables),
        updated_tables=tuple(updated_tables),
        new_columns=tuple(new_columns),
        updated_columns=tuple(updated_columns),
        deleted_tables=deleted_tables,
        deleted_columns=deleted_columns,
    )
