"""Reachability, read-only verification and schema introspection of a stored connection.

Everything here blocks (driver I/O); API handlers run it in a worker thread. Failures
are reported with fixed, generic messages: the host, path, database name, password and
driver messages never leave this module, and only exception *types* are logged.
"""

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import duckdb

from rootcause.connections.duckdb_executor import ColumnInfo, DuckDBExecutor
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
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()

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


@dataclass(frozen=True, slots=True)
class Introspection:
    """Schemas of at most ``max_tables`` tables; ``truncated`` when more tables exist."""

    schemas: tuple[SchemaInfo, ...]
    truncated: bool


def _unreachable(message: str) -> ProbeOutcome:
    return ProbeOutcome(reachable=False, read_only=False, server_version=None, error=message)


def _verification_failed() -> ProbeOutcome:
    return ProbeOutcome(
        reachable=True, read_only=False, server_version=None, error=VERIFICATION_FAILED_MESSAGE
    )


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


def _file_lock(record: ConnectionRecord) -> threading.Lock:
    """One lock per DuckDB file for the short-lived opens of this module.

    Connections to one file share a DuckDB instance in a process, and a second
    executor cannot open while the first one holds the instance's locked
    configuration (``duckdb_lockdown``); concurrent probes and imports of one file
    therefore take turns instead of failing.
    """
    key = str(Path(record.path or "").resolve())
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.Lock())


def _probe_duckdb(record: ConnectionRecord) -> ProbeOutcome:
    with _file_lock(record):
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
    except RootCauseError as error:  # logged in, then failed (timeout, permission, ...)
        _log_failure("connection_probe_open_failed", record, error)
        return _verification_failed()
    with executor:
        try:
            version = executor.server_version()
            read_only = executor.is_read_only()
        except RootCauseError as error:
            _log_failure("connection_probe_check_failed", record, error)
            return _verification_failed()
    return ProbeOutcome(reachable=True, read_only=read_only, server_version=version, error=None)


def introspect(
    record: ConnectionRecord, cipher: SecretCipher | None, *, max_tables: int
) -> Introspection:
    """Schemas with the first ``max_tables`` tables in (schema, table) order, their row
    estimates and columns.

    Raises:
        ConnectionUnavailable: the source cannot be opened.
    """
    if max_tables < 1:
        raise ValueError("max_tables must be at least 1")
    if record.kind == "duckdb":
        with _file_lock(record), open_duckdb(record) as duck:
            return _duckdb_introspection(duck, max_tables)
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
        page = executor.introspect(max_tables)
    tables = (
        (relation.schema, TableSchema(relation.name, relation.row_estimate, relation.columns))
        for relation in page.relations
    )
    return Introspection(schemas=_grouped(tables), truncated=page.truncated)


def _duckdb_introspection(duck: DuckDBExecutor, max_tables: int) -> Introspection:
    """DuckDB tables are bare names in schema ``main``; one ``describe_table`` per kept table."""
    listed = sorted(duck.list_tables(), key=lambda table: table.name)
    tables = (
        (DUCKDB_SCHEMA, TableSchema(table.name, table.row_count, duck.describe_table(table.name)))
        for table in listed[:max_tables]
    )
    return Introspection(schemas=_grouped(tables), truncated=len(listed) > max_tables)


def _grouped(tables: Iterable[tuple[str, TableSchema]]) -> tuple[SchemaInfo, ...]:
    """Tables grouped by schema; schemas and tables sorted by name."""
    grouped: dict[str, list[TableSchema]] = {}
    for schema, table in tables:
        grouped.setdefault(schema, []).append(table)
    return tuple(
        SchemaInfo(name=schema, tables=tuple(sorted(members, key=lambda t: t.name)))
        for schema, members in sorted(grouped.items())
    )
