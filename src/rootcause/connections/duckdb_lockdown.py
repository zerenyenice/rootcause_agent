"""Connection hardening for the DuckDB warehouse executor.

Settings are applied with ``SET`` right after connecting, ``lock_configuration`` is
set last so no later statement can undo them, and the values are then re-read and
verified; any mismatch closes the connection and raises (fail closed). Setting names
and values are module constants or validated integers; nothing here comes from input.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

import duckdb

LOCKDOWN_THREADS = 2
MEMORY_LIMIT_SETTING = "memory_limit"
LOCK_SETTING = "lock_configuration"

# name -> (SQL literal for SET, value as rendered by duckdb_settings(), lower-cased)
_FIXED: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "enable_external_access": ("false", "false"),
        "autoinstall_known_extensions": ("false", "false"),
        "autoload_known_extensions": ("false", "false"),
        "python_enable_replacements": ("false", "false"),
        "allow_persistent_secrets": ("false", "false"),
        "threads": (str(LOCKDOWN_THREADS), str(LOCKDOWN_THREADS)),
        "max_temp_directory_size": ("'0B'", "0 bytes"),
    }
)
SNAPSHOT_SETTINGS: tuple[str, ...] = (*_FIXED, MEMORY_LIMIT_SETTING, LOCK_SETTING)
SETTINGS_SQL = "SELECT name, value FROM duckdb_settings() WHERE name IN ?"

_BYTES_PER_MIB = 1024 * 1024
# DuckDB renders sizes with one decimal ("976.5 MiB"); 1 % absorbs that rounding.
_MEMORY_TOLERANCE = 0.01
_SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z]+)\s*$")
_UNIT_BYTES: Mapping[str, int] = MappingProxyType(
    {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
)


class LockdownFailed(RuntimeError):
    """The hardened settings could not be applied or verified."""

    def __init__(self) -> None:
        super().__init__("The warehouse connection could not be locked down.")


def current_settings(connection: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """The lockdown settings as rendered by DuckDB, values lower-cased."""
    rows = connection.execute(SETTINGS_SQL, [list(SNAPSHOT_SETTINGS)]).fetchall()
    return {str(name): str(value).lower() for name, value in rows}


def _rendered_bytes(rendered: str | None) -> float | None:
    match = _SIZE_PATTERN.match(rendered or "")
    if match is None or match.group(2) not in _UNIT_BYTES:
        return None
    return float(match.group(1)) * _UNIT_BYTES[match.group(2)]


def _memory_matches(rendered: str | None, memory_limit_mb: int) -> bool:
    expected = memory_limit_mb * _BYTES_PER_MIB
    actual = _rendered_bytes(rendered)
    return actual is not None and abs(actual - expected) <= expected * _MEMORY_TOLERANCE


def _is_locked_down(settings: Mapping[str, str], memory_limit_mb: int) -> bool:
    return (
        all(settings.get(name) == expected for name, (_, expected) in _FIXED.items())
        and _memory_matches(settings.get(MEMORY_LIMIT_SETTING), memory_limit_mb)
        and settings.get(LOCK_SETTING) == "true"
    )


def apply_lockdown(connection: duckdb.DuckDBPyConnection, memory_limit_mb: int) -> None:
    """Harden ``connection``, lock the configuration last, then verify (fail closed).

    An already locked instance carrying the hardened values is accepted unchanged.
    """
    if memory_limit_mb < 1:
        raise ValueError("memory_limit_mb must be at least 1")
    current = current_settings(connection)
    for name, (literal, expected) in _FIXED.items():
        if current.get(name) != expected:
            connection.execute(f"SET {name} = {literal}")
    if not _memory_matches(current.get(MEMORY_LIMIT_SETTING), memory_limit_mb):
        connection.execute(f"SET {MEMORY_LIMIT_SETTING} = '{int(memory_limit_mb)}MiB'")
    if current.get(LOCK_SETTING) != "true":
        connection.execute(f"SET {LOCK_SETTING} = true")
    if not _is_locked_down(current_settings(connection), memory_limit_mb):
        raise LockdownFailed()


def open_locked(path: str, memory_limit_mb: int) -> duckdb.DuckDBPyConnection:
    """Open ``path`` read-only and lock it down; the connection is closed on any failure."""
    connection = duckdb.connect(path, read_only=True)
    try:
        apply_lockdown(connection, memory_limit_mb)
    except BaseException:
        connection.close()
        raise
    return connection
