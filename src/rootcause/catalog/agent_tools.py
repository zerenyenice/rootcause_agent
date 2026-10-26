"""Agent wiring over a stored connection and its catalog (Sensitive).

- :func:`open_connection_executor` opens a connection's read-only executor only when
  the connection was verified read-only (``read_only_verified_at`` set, ADR-0002);
  otherwise :class:`ConnectionNotVerified` is raised before anything is opened.
- :func:`build_catalog_wiring` restricts that executor to the catalog's selected
  tables and columns (:class:`SelectionExecutor`), with the catalog descriptions as
  ``table_docs`` and ``search_catalog`` as an extra tool.

The pieces (:func:`load_selection`, :func:`open_record_executor`, :func:`wire`) are
public so a caller that caches graphs can re-check the verification and selection
on every run while reusing an open executor.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from langchain_core.embeddings import Embeddings
from langchain_core.tools import BaseTool

from rootcause.catalog.records import CatalogTableRecord
from rootcause.catalog.repository import CatalogRepository
from rootcause.catalog.restricted_executor import (
    Dialect,
    SelectedTable,
    SelectionExecutor,
    SourceExecutor,
    display_name,
)
from rootcause.catalog.search_tool import make_search_catalog_tool
from rootcause.catalog.service import require_connection
from rootcause.connections.postgres_executor import DEFAULT_TIMEOUT_SECONDS, ConnectionFailed
from rootcause.connections.probe import ConnectionUnavailable, open_duckdb, open_postgres
from rootcause.connections.repository import (
    LOGIN_FIELDS,
    ConnectionRecord,
    ConnectionRepository,
)
from rootcause.connections.secrets import SecretDecryptionError, cipher_from_settings
from rootcause.errors import RootCauseError
from rootcause.logging import get_logger
from rootcause.settings import Settings

_logger = get_logger(__name__)


class ConnectionNotVerified(RootCauseError):
    """The connection has no current read-only verification; agents may not use it."""

    code = "connection_not_verified"
    status_code = 409
    public_message = "The connection is not verified read-only. Test the connection first."

    def __init__(self) -> None:
        super().__init__("The connection is not verified read-only.")


@dataclass(frozen=True, slots=True)
class CatalogDoc:
    """Catalog description of a selected table and of its described, selected columns."""

    description: str
    columns: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CatalogSelection:
    """A verified connection and its selected tables (each with every column record)."""

    record: ConnectionRecord
    tables: tuple[CatalogTableRecord, ...]

    @property
    def dialect(self) -> Dialect:
        return "duckdb" if self.record.kind == "duckdb" else "postgres"

    @property
    def login_fingerprint(self) -> str:
        """Changes whenever where or as whom the connection logs in changes."""
        values = [
            value.hex() if isinstance(value, bytes) else value
            for value in (getattr(self.record, name) for name in LOGIN_FIELDS)
        ]
        return _digest([self.record.kind, *values])

    @property
    def fingerprint(self) -> str:
        """Changes whenever the login, the selection or a description changes."""
        tables = [
            [
                str(t.id),
                t.schema,
                t.name,
                t.description,
                [[str(c.id), c.name, c.description] for c in t.columns if c.selected],
            ]
            for t in self.tables
        ]
        return _digest([self.login_fingerprint, tables])


@dataclass(frozen=True, slots=True)
class CatalogWiring:
    """What the orchestrator needs for one connection."""

    executor: SelectionExecutor
    table_docs: Mapping[str, CatalogDoc]
    tools: tuple[BaseTool, ...]


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_verified(record: ConnectionRecord) -> None:
    if record.read_only_verified_at is None:
        raise ConnectionNotVerified()


def _open(record: ConnectionRecord, settings: Settings) -> SourceExecutor:
    """Open the executor (blocking); unreachable sources become ``ConnectionUnavailable``."""
    if record.kind == "duckdb":
        return open_duckdb(record)
    try:
        return open_postgres(
            record, cipher_from_settings(settings), timeout_seconds=DEFAULT_TIMEOUT_SECONDS
        )
    except (ConnectionFailed, SecretDecryptionError) as error:
        _logger.warning(
            "agent_connection_open_failed",
            connection_id=str(record.id),
            kind=record.kind,
            error_type=type(error).__name__,
        )
    raise ConnectionUnavailable()


async def open_record_executor(record: ConnectionRecord, settings: Settings) -> SourceExecutor:
    """Open a verified record's executor in a worker thread.

    Raises:
        ConnectionNotVerified: the record is not verified (checked before opening).
        ConnectionUnavailable: the source cannot be opened.
    """
    _require_verified(record)
    return await asyncio.to_thread(_open, record, settings)


async def open_connection_executor(
    connection_id: uuid.UUID, *, connections: ConnectionRepository, settings: Settings
) -> SourceExecutor:
    """The read-only executor of a stored, verified connection.

    Raises:
        ConnectionNotFound: no such connection.
        ConnectionNotVerified: not verified read-only (nothing is opened).
        ConnectionUnavailable: the source cannot be opened.
    """
    record = await require_connection(connection_id, connections)
    return await open_record_executor(record, settings)


async def load_selection(
    connection_id: uuid.UUID, *, connections: ConnectionRepository, catalog: CatalogRepository
) -> CatalogSelection:
    """The verified connection and its selected tables (nothing is opened).

    Raises:
        ConnectionNotFound: no such connection.
        ConnectionNotVerified: not verified read-only.
    """
    record = await require_connection(connection_id, connections)
    _require_verified(record)
    tables = tuple(t for t in await catalog.list_tables(connection_id) if t.selected)
    return CatalogSelection(record=record, tables=tables)


def _selected_table(dialect: Dialect, table: CatalogTableRecord) -> SelectedTable:
    return SelectedTable(
        schema=table.schema,
        name=table.name,
        display=display_name(dialect, table.schema, table.name),
        columns=tuple(c.name for c in table.columns if c.selected),
        hides_columns=any(not c.selected for c in table.columns),
    )


def _doc(table: CatalogTableRecord) -> CatalogDoc:
    columns = {c.name: c.description for c in table.columns if c.selected and c.description}
    return CatalogDoc(description=table.description or "", columns=MappingProxyType(columns))


def wire(
    selection: CatalogSelection,
    inner: SourceExecutor,
    *,
    catalog: CatalogRepository,
    embedder: Embeddings,
    settings: Settings,
) -> CatalogWiring:
    """Restrict ``inner`` to ``selection`` and add ``search_catalog`` (nothing is opened)."""
    dialect = selection.dialect
    tables = tuple(_selected_table(dialect, table) for table in selection.tables)
    docs = {
        display_name(dialect, table.schema, table.name): _doc(table) for table in selection.tables
    }
    search = make_search_catalog_tool(
        selection.record.id, catalog=catalog, embedder=embedder, settings=settings
    )
    return CatalogWiring(
        executor=SelectionExecutor(inner, tables, dialect),
        table_docs=MappingProxyType(docs),
        tools=(search,),
    )


async def build_catalog_wiring(
    connection_id: uuid.UUID,
    *,
    connections: ConnectionRepository,
    catalog: CatalogRepository,
    embedder: Embeddings,
    settings: Settings,
) -> CatalogWiring:
    """Executor restricted to the selected catalog, its docs and ``search_catalog``.

    The caller owns ``wiring.executor`` and must ``close()`` it.

    Raises:
        ConnectionNotFound: no such connection.
        ConnectionNotVerified: not verified read-only (nothing is opened).
        ConnectionUnavailable: the source cannot be opened.
    """
    selection = await load_selection(connection_id, connections=connections, catalog=catalog)
    inner = await open_record_executor(selection.record, settings)
    return wire(selection, inner, catalog=catalog, embedder=embedder, settings=settings)
