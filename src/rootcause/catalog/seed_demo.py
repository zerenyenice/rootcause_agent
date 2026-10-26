"""Register the demo manufacturing warehouse as a ready-to-use connection.

``python -m rootcause.catalog.seed_demo --path <warehouse.duckdb>`` (application
database from ``ROOTCAUSE_DATABASE_URL``) registers the DuckDB file as connection
:data:`DEMO_CONNECTION_NAME`, imports its catalog, selects every table and column,
copies descriptions from :data:`~rootcause.evals.manufacturing.table_docs.TABLE_DOCS`
and marks the connection verified only after the read-only probe succeeded.

Idempotent: a DuckDB connection with the same absolute path is reused. A file that
cannot be opened registers nothing. The warehouse is only ever opened read-only.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from rootcause.catalog.records import CatalogTableRecord
from rootcause.catalog.repository import CatalogRepository, SqlCatalogRepository
from rootcause.catalog.service import import_catalog
from rootcause.connections.probe import ConnectionUnavailable, probe
from rootcause.connections.repository import (
    ConnectionRecord,
    ConnectionRepository,
    NewConnection,
    SqlConnectionRepository,
)
from rootcause.db.engine import make_async_engine
from rootcause.errors import RootCauseError
from rootcause.evals.manufacturing.table_docs import TABLE_DOCS
from rootcause.logging import configure_logging, get_logger
from rootcause.settings import Settings, get_settings

DEMO_CONNECTION_NAME: Final = "Demo manufacturing warehouse"

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SeedResult:
    """The demo connection and the totals of its catalog."""

    connection_id: uuid.UUID
    tables: int
    columns: int


def _transient(path: Path) -> ConnectionRecord:
    """An unsaved record of the file, to probe it before registering anything."""
    now = datetime.now(UTC)
    return ConnectionRecord(
        id=uuid.uuid4(),
        name=DEMO_CONNECTION_NAME,
        kind="duckdb",
        host=None,
        port=None,
        database=None,
        user=None,
        sslmode=None,
        path=str(path),
        encrypted_secret=None,
        read_only_verified_at=None,
        created_at=now,
        updated_at=now,
    )


async def _is_read_only(record: ConnectionRecord) -> bool:
    outcome = await asyncio.to_thread(probe, record, None)
    return outcome.reachable and outcome.read_only


async def _demo_connection(path: Path, connections: ConnectionRepository) -> ConnectionRecord:
    for record in await connections.list():
        if record.kind == "duckdb" and record.path == str(path):
            return record
    return await connections.create(
        NewConnection(name=DEMO_CONNECTION_NAME, kind="duckdb", path=str(path))
    )


async def _select_and_describe(catalog: CatalogRepository, table: CatalogTableRecord) -> None:
    doc = TABLE_DOCS.get(table.name)
    changes: dict[str, object] = {"selected": True}
    if doc is not None:
        changes["description"] = doc.description
    await catalog.update_table(table.id, changes)
    for column in table.columns:
        column_changes: dict[str, object] = {"selected": True}
        described = doc.columns.get(column.name) if doc is not None else None
        if described is not None:
            column_changes["description"] = described
        await catalog.update_column(column.id, column_changes)


async def seed_demo(
    path: Path,
    *,
    connections: ConnectionRepository,
    catalog: CatalogRepository,
    settings: Settings,
) -> SeedResult:
    """Register, import, select, describe and verify the demo warehouse at ``path``.

    Raises:
        ConnectionUnavailable: the file cannot be opened read-only as DuckDB (nothing
            is registered).
    """
    resolved = Path(path).resolve()
    if not await _is_read_only(_transient(resolved)):
        raise ConnectionUnavailable()
    record = await _demo_connection(resolved, connections)
    counts = await import_catalog(
        record.id, connections=connections, catalog=catalog, settings=settings
    )
    for table in await catalog.list_tables(record.id):
        await _select_and_describe(catalog, table)
    if await _is_read_only(record):
        await connections.record_verification(record, datetime.now(UTC))
    _logger.info(
        "demo_warehouse_seeded",
        connection_id=str(record.id),
        tables=counts.tables,
        columns=counts.columns,
    )
    return SeedResult(connection_id=record.id, tables=counts.tables, columns=counts.columns)


async def _seed_application_database(path: Path, settings: Settings) -> SeedResult:
    engine = make_async_engine(settings)
    try:
        return await seed_demo(
            path,
            connections=SqlConnectionRepository(engine),
            catalog=SqlCatalogRepository(engine),
            settings=settings,
        )
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rootcause.catalog.seed_demo",
        description="Register the demo DuckDB warehouse as a verified, fully selected connection.",
    )
    parser.add_argument("--path", required=True, type=Path, help="the warehouse .duckdb file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code (argument errors exit with 2)."""
    arguments = _parser().parse_args(argv)
    try:
        settings = get_settings()
        configure_logging(settings)
        result = asyncio.run(_seed_application_database(arguments.path, settings))
    except RootCauseError as error:
        print(f"error: {error.public_message}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - report the type only: messages name hosts
        print(
            f"error: the demo warehouse could not be seeded ({type(error).__name__})",
            file=sys.stderr,
        )
        return 1
    summary = f"{result.tables} tables, {result.columns} columns"
    print(f"seeded connection {result.connection_id}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
