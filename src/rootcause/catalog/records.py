"""Immutable records of the metadata catalog, shared by both repositories and the service."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

# Fields an analyst may edit; everything else comes from the source on import.
EDITABLE_FIELDS = frozenset({"selected", "description"})
EntryKind = Literal["table", "column"]


@dataclass(frozen=True, slots=True)
class CatalogColumnRecord:
    """A column of a catalog table; ``ordinal`` is its 1-based declaration position."""

    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    data_type: str
    nullable: bool
    ordinal: int
    selected: bool
    description: str | None


@dataclass(frozen=True, slots=True)
class CatalogTableRecord:
    """A table of a connection's catalog with its columns in declaration order."""

    id: uuid.UUID
    connection_id: uuid.UUID
    schema: str
    name: str
    row_estimate: int | None
    selected: bool
    description: str | None
    columns: tuple[CatalogColumnRecord, ...]


@dataclass(frozen=True, slots=True)
class CatalogCounts:
    """How many tables and columns (an import's totals, or an index's entries)."""

    tables: int
    columns: int


@dataclass(frozen=True, slots=True)
class EmbeddingEntry:
    """One vector to store: a table (``column_id`` is ``None``) or one of its columns."""

    table_id: uuid.UUID
    column_id: uuid.UUID | None
    content: str
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A catalog entry similar to a search query; ``score`` is the cosine similarity."""

    kind: EntryKind
    schema: str
    table: str
    column: str | None
    description: str | None
    score: float


def checked_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    """``changes`` if it only touches editable fields; ``ValueError`` otherwise."""
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")
    return dict(changes)
