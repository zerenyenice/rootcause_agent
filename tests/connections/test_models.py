"""Metadata-level checks for the connections and audit-log models (no database)."""

import pytest


@pytest.fixture
def connections_table():
    from rootcause.connections.models import Connection

    return Connection.__table__


@pytest.fixture
def query_log_table():
    from rootcause.connections.models import QueryAuditLog

    return QueryAuditLog.__table__


def test_models_share_the_core_base_metadata():
    from rootcause.connections.models import Connection, QueryAuditLog
    from rootcause.db.base import Base

    assert Connection.metadata is Base.metadata
    assert QueryAuditLog.metadata is Base.metadata


def test_connection_table_lives_in_the_app_schema(connections_table):
    assert connections_table.name == "connections"
    assert connections_table.schema == "app"


def test_connection_columns(connections_table):
    expected = {
        "id",
        "display_name",
        "dialect",
        "host",
        "port",
        "database",
        "username",
        "encrypted_secret",
        "sslmode",
        "path",
        "limits",
        "read_only_verified_at",
        "created_at",
        "updated_at",
        "tenant_id",
    }

    assert set(connections_table.columns.keys()) == expected


def test_connection_primary_key_is_a_uuid_id(connections_table):
    from sqlalchemy.dialects.postgresql import UUID

    assert [c.name for c in connections_table.primary_key.columns] == ["id"]
    assert isinstance(connections_table.c.id.type, UUID)


def test_connection_secret_is_binary_and_limits_are_jsonb(connections_table):
    from sqlalchemy import LargeBinary
    from sqlalchemy.dialects.postgresql import JSONB

    assert isinstance(connections_table.c.encrypted_secret.type, LargeBinary)
    assert isinstance(connections_table.c.limits.type, JSONB)


@pytest.mark.parametrize(
    "column",
    ["display_name", "dialect"],
)
def test_connection_required_columns_are_not_nullable(connections_table, column):
    assert connections_table.c[column].nullable is False


@pytest.mark.parametrize(
    "column",
    ["host", "port", "database", "username", "encrypted_secret"],
)
def test_connection_network_and_secret_columns_are_nullable(connections_table, column):
    # DuckDB connections are file-based and carry no network or secret fields.
    assert connections_table.c[column].nullable is True


def test_connection_read_only_verified_at_is_nullable(connections_table):
    assert connections_table.c.read_only_verified_at.nullable is True


def test_connection_timestamps_are_timezone_aware(connections_table):
    assert connections_table.c.created_at.type.timezone is True
    assert connections_table.c.updated_at.type.timezone is True
    assert connections_table.c.read_only_verified_at.type.timezone is True


def test_query_log_table_lives_in_the_audit_schema(query_log_table):
    assert query_log_table.name == "query_log"
    assert query_log_table.schema == "audit"


def test_query_log_columns_and_append_only_shape(query_log_table):
    expected = {
        "id",
        "connection_id",
        "thread_id",
        "agent",
        "purpose",
        "sql",
        "started_at",
        "duration_ms",
        "row_count",
        "status",
        "error_code",
        "request_id",
        "tenant_id",
    }

    assert set(query_log_table.columns.keys()) == expected
    assert "updated_at" not in query_log_table.columns


def test_query_log_primary_key_is_a_bigint_identity(query_log_table):
    from sqlalchemy import BigInteger

    id_column = query_log_table.c.id
    assert [c.name for c in query_log_table.primary_key.columns] == ["id"]
    assert isinstance(id_column.type, BigInteger)
    assert id_column.identity is not None


def test_query_log_connection_fk_targets_app_connections(query_log_table):
    foreign_keys = list(query_log_table.c.connection_id.foreign_keys)

    assert [fk.target_fullname for fk in foreign_keys] == ["app.connections.id"]
    assert foreign_keys[0].constraint is not None
    assert foreign_keys[0].constraint.name == "fk_query_log_connection_id_connections"


def test_query_log_status_allows_exactly_ok_error_rejected(query_log_table):
    status_type = query_log_table.c.status.type

    assert list(status_type.enums) == ["ok", "error", "rejected"]
    assert query_log_table.c.status.nullable is False


def test_query_log_has_connection_started_at_index(query_log_table):
    indexes = [[c.name for c in index.columns] for index in query_log_table.indexes]

    assert ["connection_id", "started_at"] in indexes


def test_query_log_required_columns_are_not_nullable(query_log_table):
    for column in ("connection_id", "sql", "started_at", "status"):
        assert query_log_table.c[column].nullable is False
    assert query_log_table.c.error_code.nullable is True


# --- amendment: model hardening ---------------------------------------------


def test_connection_id_has_a_database_side_uuid_default(connections_table):
    default = connections_table.c.id.server_default

    assert default is not None
    assert "gen_random_uuid" in str(default.arg)


def test_connection_updated_at_refreshes_on_update(connections_table):
    assert connections_table.c.updated_at.onupdate is not None


def test_connection_port_is_constrained_to_valid_tcp_ports(connections_table):
    from sqlalchemy import CheckConstraint

    checks = [
        str(c.sqltext) for c in connections_table.constraints if isinstance(c, CheckConstraint)
    ]

    assert any("port" in text and "1" in text and "65535" in text for text in checks), checks


def test_query_status_is_a_str_enum_with_the_three_values():
    from enum import StrEnum

    from rootcause.connections.models import QueryStatus

    assert issubclass(QueryStatus, StrEnum)
    assert [s.value for s in QueryStatus] == ["ok", "error", "rejected"]
    assert str(QueryStatus.OK) == "ok"
    assert QueryStatus("ok") is QueryStatus.OK


def test_query_log_status_maps_the_enum_on_the_query_status_type(query_log_table):
    from sqlalchemy import Enum

    from rootcause.connections.models import QueryStatus

    status_type = query_log_table.c.status.type

    assert isinstance(status_type, Enum)
    assert status_type.name == "query_status"
    assert status_type.schema == "audit"
    assert status_type.enum_class is QueryStatus
    assert list(status_type.enums) == ["ok", "error", "rejected"]
