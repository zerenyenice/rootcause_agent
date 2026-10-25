"""Catalog use cases: import a connection's metadata and build its vector index.

Indexing is synchronous for the MVP: :func:`build_index` embeds and stores
everything before it returns, so its status is ``done`` or ``failed``. A failed run
writes nothing, leaving the previous index in effect. Failures are logged by
exception type only: provider and driver messages can carry endpoints or names.
"""

import asyncio
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from langchain_core.embeddings import Embeddings

from rootcause.catalog.records import CatalogCounts, CatalogTableRecord, EmbeddingEntry
from rootcause.catalog.repository import CatalogRepository
from rootcause.connections.probe import introspect
from rootcause.connections.repository import (
    ConnectionNotFound,
    ConnectionRecord,
    ConnectionRepository,
)
from rootcause.connections.secrets import SecretCipher, cipher_from_settings
from rootcause.logging import get_logger
from rootcause.settings import Settings

_logger = get_logger(__name__)

IndexState = Literal["idle", "running", "done", "failed"]


@dataclass(frozen=True, slots=True)
class IndexStatus:
    """The outcome of an index run, or the index currently in effect."""

    status: IndexState
    indexed_tables: int
    indexed_columns: int


FAILED = IndexStatus(status="failed", indexed_tables=0, indexed_columns=0)


async def require_connection(
    connection_id: uuid.UUID, connections: ConnectionRepository
) -> ConnectionRecord:
    """The stored connection; :class:`ConnectionNotFound` when there is none."""
    record = await connections.get(connection_id)
    if record is None:
        raise ConnectionNotFound()
    return record


def cipher_for(record: ConnectionRecord, settings: Settings) -> SecretCipher | None:
    """The secret cipher for a Postgres connection; DuckDB connections have no secret."""
    return cipher_from_settings(settings) if record.kind == "postgres" else None


async def import_catalog(
    connection_id: uuid.UUID,
    *,
    connections: ConnectionRepository,
    catalog: CatalogRepository,
    settings: Settings,
) -> CatalogCounts:
    """Introspect the connection (in a worker thread) and upsert its catalog.

    Raises:
        ConnectionNotFound: no such connection (also when it is deleted meanwhile).
        ConnectionUnavailable: the source cannot be opened; the catalog is unchanged.
    """
    record = await require_connection(connection_id, connections)
    introspection = await asyncio.to_thread(
        partial(introspect, max_tables=settings.introspect_max_tables),
        record,
        cipher_for(record, settings),
    )
    counts = await catalog.apply_import(connection_id, introspection)
    if counts is None:
        raise ConnectionNotFound()
    _logger.info(
        "catalog_imported",
        connection_id=str(connection_id),
        tables=counts.tables,
        columns=counts.columns,
        truncated=introspection.truncated,
    )
    return counts


def _table_text(table: CatalogTableRecord) -> str:
    text = f"Table {table.schema}.{table.name}"
    return f"{text}: {table.description}" if table.description else text


def index_documents(
    tables: Sequence[CatalogTableRecord],
) -> tuple[tuple[uuid.UUID, uuid.UUID | None, str], ...]:
    """(table id, column id, text) per selected table and selected column of one."""
    documents: list[tuple[uuid.UUID, uuid.UUID | None, str]] = []
    for table in tables:
        if not table.selected:
            continue
        documents.append((table.id, None, _table_text(table)))
        for column in table.columns:
            if not column.selected:
                continue
            text = f"Column {table.schema}.{table.name}.{column.name} ({column.data_type})"
            if column.description:
                text = f"{text}: {column.description}"
            documents.append((table.id, column.id, text))
    return tuple(documents)


def _valid_vectors(vectors: Sequence[Sequence[float]], count: int, dimensions: int) -> bool:
    if len(vectors) != count:
        return False
    return all(
        len(vector) == dimensions and all(math.isfinite(value) for value in vector)
        for vector in vectors
    )


async def _embed(embedder: Embeddings, texts: list[str], settings: Settings) -> list[list[float]]:
    if not texts:
        return []
    vectors = await embedder.aembed_documents(texts)
    if not _valid_vectors(vectors, len(texts), settings.embedding_dimensions):
        raise ValueError("the embeddings do not match the configured dimension")
    return vectors


async def build_index(
    connection_id: uuid.UUID,
    *,
    connections: ConnectionRepository,
    catalog: CatalogRepository,
    embedder: Embeddings,
    settings: Settings,
) -> IndexStatus:
    """Embed the selected tables and columns and replace the connection's index.

    Returns ``failed`` (zero counts, previous index kept) when embedding or storing
    fails, or when a vector's length differs from ``settings.embedding_dimensions``.

    Raises:
        ConnectionNotFound: no such connection.
    """
    await require_connection(connection_id, connections)
    documents = index_documents(await catalog.list_tables(connection_id))
    try:
        vectors = await _embed(embedder, [text for *_, text in documents], settings)
        entries = [
            EmbeddingEntry(table_id, column_id, text, tuple(vector))
            for (table_id, column_id, text), vector in zip(documents, vectors, strict=True)
        ]
        await catalog.replace_embeddings(connection_id, entries)
    except Exception as error:  # noqa: BLE001 - reported as a failed run, type only
        _logger.warning(
            "catalog_index_failed",
            connection_id=str(connection_id),
            error_type=type(error).__name__,
        )
        return FAILED
    columns = sum(1 for _, column_id, _ in documents if column_id is not None)
    _logger.info(
        "catalog_indexed",
        connection_id=str(connection_id),
        tables=len(documents) - columns,
        columns=columns,
    )
    return IndexStatus(
        status="done", indexed_tables=len(documents) - columns, indexed_columns=columns
    )


async def index_status(
    connection_id: uuid.UUID,
    *,
    connections: ConnectionRepository,
    catalog: CatalogRepository,
) -> IndexStatus:
    """``done`` with the stored counts, or ``idle`` while nothing is indexed.

    Raises:
        ConnectionNotFound: no such connection.
    """
    await require_connection(connection_id, connections)
    counts = await catalog.index_counts(connection_id)
    if counts.tables == 0 and counts.columns == 0:
        return IndexStatus(status="idle", indexed_tables=0, indexed_columns=0)
    return IndexStatus(status="done", indexed_tables=counts.tables, indexed_columns=counts.columns)
