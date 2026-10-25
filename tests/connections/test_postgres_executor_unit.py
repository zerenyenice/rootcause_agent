"""Postgres read-only executor: construction and failure modes without a server (RED).

The behaviour against a real server lives in
``tests/db/integration/test_postgres_executor.py``.
"""

import traceback
from typing import Any

import pytest

from rootcause.connections import postgres_executor as module
from rootcause.connections.postgres_executor import ConnectionFailed, PostgresExecutor
from rootcause.errors import RootCauseError

pytestmark = pytest.mark.unit

PASSWORD = "SENTINEL-pw-123"  # gitleaks:allow
CLOSED_PORT = 1


def _unreachable(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": CLOSED_PORT,
        "database": "warehouse",
        "user": "analyst",
        "password": PASSWORD,
        "sslmode": "disable",
    }
    params.update(overrides)
    return params


def test_unreachable_server_raises_connection_failed_without_secrets() -> None:
    with pytest.raises(ConnectionFailed) as caught:
        PostgresExecutor(**_unreachable())

    exc = caught.value
    assert isinstance(exc, RootCauseError)
    assert exc.code == "connection_failed"
    assert exc.__cause__ is None
    rendered = "".join(traceback.format_exception(exc)) + repr(exc) + exc.public_message
    assert PASSWORD not in rendered
    assert "SENTINEL" not in rendered
    assert "postgresql://" not in rendered


@pytest.mark.parametrize(
    "overrides",
    [{"max_rows": 0}, {"timeout_seconds": 0}, {"timeout_seconds": -1.0}],
)
def test_invalid_limits_raise_value_error_before_connecting(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PostgresExecutor(**_unreachable(**overrides))


def test_executor_errors_are_rootcause_errors_with_stable_codes() -> None:
    assert issubclass(module.QueryRejected, RootCauseError)
    assert issubclass(module.QueryFailed, RootCauseError)
    assert issubclass(module.QueryTimeout, RootCauseError)
    assert issubclass(module.UnknownTable, RootCauseError)
    assert module.QueryRejected.code == "query_rejected"
    assert module.QueryTimeout.code == "query_timeout"
    assert module.UnknownTable.code == "unknown_table"
    assert ConnectionFailed.code == "connection_failed"


def test_executor_uses_the_postgres_dialect() -> None:
    assert module.DIALECT == "postgres"
