"""Metadata-level checks for the catalog version model (no database)."""

import pytest


@pytest.fixture
def versions_table():
    from rootcause.catalog.models import CatalogVersion

    return CatalogVersion.__table__


def test_model_shares_the_core_base_metadata():
    from rootcause.catalog.models import CatalogVersion
    from rootcause.db.base import Base

    assert CatalogVersion.metadata is Base.metadata


def test_versions_table_lives_in_the_catalog_schema(versions_table):
    assert versions_table.name == "versions"
    assert versions_table.schema == "catalog"


def test_versions_columns(versions_table):
    expected = {"id", "connection_id", "version", "published_at", "created_by", "notes"}

    assert set(versions_table.columns.keys()) == expected


def test_versions_primary_key_is_id(versions_table):
    assert [c.name for c in versions_table.primary_key.columns] == ["id"]


def test_versions_connection_fk_targets_app_connections(versions_table):
    foreign_keys = list(versions_table.c.connection_id.foreign_keys)

    assert [fk.target_fullname for fk in foreign_keys] == ["app.connections.id"]
    assert versions_table.c.connection_id.nullable is False


def test_version_is_an_integer_and_required(versions_table):
    from sqlalchemy import Integer

    assert isinstance(versions_table.c.version.type, Integer)
    assert versions_table.c.version.nullable is False


def test_unpublished_versions_are_allowed(versions_table):
    assert versions_table.c.published_at.nullable is True


def test_connection_and_version_are_unique_together(versions_table):
    from sqlalchemy import UniqueConstraint

    unique_sets = [
        [c.name for c in constraint.columns]
        for constraint in versions_table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]

    assert ["connection_id", "version"] in unique_sets


def test_unique_constraint_follows_the_naming_convention(versions_table):
    from sqlalchemy import UniqueConstraint

    names = {
        constraint.name
        for constraint in versions_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "uq_versions_connection_id_version" in names
