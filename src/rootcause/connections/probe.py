"""Reachability, read-only verification and schema introspection of a stored connection.

Everything here blocks (driver I/O); API handlers run it in a worker thread. Failures
are reported with fixed, generic messages: the host, path, database name, password and
driver messages never leave this module, and only exception *types* are logged.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import duckdb

from rootcause.connections.duckdb_executor import ColumnInfo, DuckDBExecutor, TableInfo
from rootcause.connections.postgres_executor import ConnectionFailed, PostgresExecutor
from rootcause.connections.repository import ConnectionRecord
from rootcause.connections.secrets import SecretCipher, SecretDecryptionError
from rootcause.errors import RootCauseError
from rootcause.logging import get_logger

_logger = get_logger(__name__)

# Contract: a probe never waits longer than this for the server to accept the login.
PROBE_CONNECT_TIMEOUT_SECONDS = 5
PROBE_QUERY_TIMEOUT_SECONDS = 15.0
DUCKDB_SCHEMA = "main"

UNREACHABLE_MESSAGE = (
    "Could not connect. Check the host, port, database, user, password and SSL mode."
)
FILE_UNREADABLE_MESSAGE = "The file could not be opened as a DuckDB database."
VERIFICATION_FAILED_MESSAGE = "Connected, but the read-only check could not be completed."
UNDECRYPTABLE_MESSAGE = "The stored password could not be decrypted; set the password again."


class ConnectionUnavailable(RootCauseError):
    """The data source could not be opened (unreachable server, missing or invalid file)."""

    code = "connection_unavailable"
    status_code = 503
    public_message = "The data source could not be reached."

    def __init__(self) -> None:
        super().__init__("The data source could not be reached.")


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """Result of ``POST /connections/{id}/test``."""

    reachable: bool
    read_only: bool
    server_version: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class TableSchema:
    name: str
    row_estimate: int
    columns: tuple[ColumnInfo, ...]


@dataclass(frozen=True, slots=True)
class SchemaInfo:
    name: str
    tables: tuple[TableSchema, ...]


class _Introspectable(Protocol):
    def list_tables(self) -> Sequence[TableInfo]: ...

    def describe_table(self, name: str) -> Sequence[ColumnInfo]: ...


def _unreachable(message: str) -> ProbeOutcome:
    return ProbeOutcome(reachable=False, read_only=False, server_version=None, error=message)


def _log_failure(event: str, record: ConnectionRecord, error: BaseException) -> None:
    _logger.warning(
        event, connection_id=str(record.id), kind=record.kind, error_type=type(error).__name__
    )


def open_postgres(
    record: ConnectionRecord, cipher: SecretCipher, *, timeout_seconds: float
) -> PostgresExecutor:
    """Executor for a stored Postgres connection (decrypts the password just for the login).

    Raises:
        ConnectionFailed: the server is unreachable or refused the login.
        SecretDecryptionError: the stored password cannot be decrypted.
    """
    if record.encrypted_secret is None or record.host is None or record.port is None:
        raise ConnectionFailed()
    return PostgresExecutor(
        host=record.host,
        port=record.port,
        database=record.database or "",
        user=record.user or "",
        password=cipher.decrypt(record.encrypted_secret),
        sslmode=record.sslmode or "prefer",
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=PROBE_CONNECT_TIMEOUT_SECONDS,
    )


def open_duckdb(record: ConnectionRecord) -> DuckDBExecutor:
    """Executor for a stored DuckDB connection (read-only, locked down).

    Raises:
        ConnectionUnavailable: the file is missing or not a DuckDB database.
    """
    try:
        return DuckDBExecutor(Path(record.path or ""))
    except (OSError, duckdb.Error) as error:
        _log_failure("connection_open_failed", record, error)
    raise ConnectionUnavailable()


def probe(record: ConnectionRecord, cipher: SecretCipher | None) -> ProbeOutcome:
    """Open the connection and verify it is read-only; never raises for an unreachable source.

    ``cipher`` is required for Postgres connections (their password is encrypted).
    """
    if record.kind == "duckdb":
        return _probe_duckdb(record)
    if cipher is None:
        raise ValueError("a cipher is required to probe a Postgres connection")
    return _probe_postgres(record, cipher)


def _probe_duckdb(record: ConnectionRecord) -> ProbeOutcome:
    try:
        executor = open_duckdb(record)
    except ConnectionUnavailable:
        return _unreachable(FILE_UNREADABLE_MESSAGE)
    with executor:
        # Opened read-only with external access, ATTACH and extensions locked out.
        return ProbeOutcome(
            reachable=True, read_only=True, server_version=duckdb.__version__, error=None
        )


def _probe_postgres(record: ConnectionRecord, cipher: SecretCipher) -> ProbeOutcome:
    try:
        executor = open_postgres(record, cipher, timeout_seconds=PROBE_QUERY_TIMEOUT_SECONDS)
    except ConnectionFailed as error:
        _log_failure("connection_probe_unreachable", record, error)
        return _unreachable(UNREACHABLE_MESSAGE)
    except SecretDecryptionError as error:
        _log_failure("connection_probe_undecryptable", record, error)
        return _unreachable(UNDECRYPTABLE_MESSAGE)
    with executor:
        try:
            version = executor.server_version()
            read_only = executor.is_read_only()
        except RootCauseError as error:
            _log_failure("connection_probe_check_failed", record, error)
            return ProbeOutcome(
                reachable=True,
                read_only=False,
                server_version=None,
                error=VERIFICATION_FAILED_MESSAGE,
            )
    return ProbeOutcome(reachable=True, read_only=read_only, server_version=version, error=None)


def introspect(record: ConnectionRecord, cipher: SecretCipher | None) -> tuple[SchemaInfo, ...]:
    """Schemas with their tables (sorted by name), row estimates and columns.

    Raises:
        ConnectionUnavailable: the source cannot be opened.
    """
    if record.kind == "duckdb":
        with open_duckdb(record) as duck:
            return _schemas(duck, qualified=False)
    if cipher is None:
        raise ValueError("a cipher is required to introspect a Postgres connection")
    try:
        executor = open_postgres(record, cipher, timeout_seconds=PROBE_QUERY_TIMEOUT_SECONDS)
    except ConnectionFailed as error:
        _log_failure("connection_introspect_unreachable", record, error)
        executor = None
    if executor is None:
        raise ConnectionUnavailable()
    with executor:
        return _schemas(executor, qualified=True)


def _schemas(executor: _Introspectable, *, qualified: bool) -> tuple[SchemaInfo, ...]:
    """Group ``list_tables`` by schema; DuckDB names are bare (schema ``main``)."""
    grouped: dict[str, list[TableSchema]] = {}
    for table in executor.list_tables():
        schema, _, name = table.name.rpartition(".") if qualified else ("", "", table.name)
        columns = tuple(executor.describe_table(table.name))
        grouped.setdefault(schema or DUCKDB_SCHEMA, []).append(
            TableSchema(name=name, row_estimate=table.row_count, columns=columns)
        )
    return tuple(
        SchemaInfo(name=schema, tables=tuple(sorted(tables, key=lambda t: t.name)))
        for schema, tables in sorted(grouped.items())
    )
