"""Metadata of the catalog tables, columns and embeddings models (RED; no database).

``catalog.tables`` and ``catalog.columns`` hold the imported metadata with the
analyst's ``selected`` flag and description; ``catalog.embeddings`` holds one pgvector
embedding per indexed table or column. Deleting a connection removes its catalog
(``ON DELETE CASCADE`` down the chain), so the catalog never blocks a connection delete.
The embedding column has no fixed dimension in the schema: the dimension is a setting
(``embedding_dimensions``) checked by the indexer.
"""

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _fk(table: Any, column: str) -> Any:
    [foreign_key] = list(table.c[column].foreign_keys)
    return foreign_key


def _unique_sets(table: Any) -> set[frozenset[str]]:
    from sqlalchemy import UniqueConstraint

    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


@pytest.fixture
def tables_table() -> Any:
    from rootcause.catalog.models import CatalogTable

    return CatalogTable.__table__


@pytest.fixture
def columns_table() -> Any:
    from rootcause.catalog.models import CatalogColumn

    return CatalogColumn.__table__


@pytest.fixture
def embeddings_table() -> Any:
    from rootcause.catalog.models import CatalogEmbedding

    return CatalogEmbedding.__table__


def test_models_live_in_the_catalog_schema(
    tables_table: Any, columns_table: Any, embeddings_table: Any
) -> None:
    from rootcause.db.base import Base

    assert (tables_table.schema, tables_table.name) == ("catalog", "tables")
    assert (columns_table.schema, columns_table.name) == ("catalog", "columns")
    assert (embeddings_table.schema, embeddings_table.name) == ("catalog", "embeddings")
    for table in (tables_table, columns_table, embeddings_table):
        assert table.metadata is Base.metadata


def test_tables_columns_and_constraints(tables_table: Any) -> None:
    expected = {"id", "connection_id", "schema_name", "name", "selected", "description"}
    assert expected | {"row_estimate"} <= set(tables_table.columns.keys())
    assert [c.name for c in tables_table.primary_key.columns] == ["id"]
    foreign_key = _fk(tables_table, "connection_id")
    assert foreign_key.target_fullname == "app.connections.id"
    assert foreign_key.ondelete == "CASCADE"
    assert frozenset({"connection_id", "schema_name", "name"}) in _unique_sets(tables_table)
    for required in ("connection_id", "schema_name", "name", "selected"):
        assert tables_table.c[required].nullable is False, required
    assert tables_table.c.description.nullable is True
    assert tables_table.c.row_estimate.nullable is True
    assert tables_table.c.selected.server_default is not None


def test_columns_columns_and_constraints(columns_table: Any) -> None:
    expected = {"id", "table_id", "name", "data_type", "nullable", "ordinal", "selected"}
    assert expected | {"description"} <= set(columns_table.columns.keys())
    assert [c.name for c in columns_table.primary_key.columns] == ["id"]
    foreign_key = _fk(columns_table, "table_id")
    assert foreign_key.target_fullname == "catalog.tables.id"
    assert foreign_key.ondelete == "CASCADE"
    assert frozenset({"table_id", "name"}) in _unique_sets(columns_table)
    for required in ("table_id", "name", "data_type", "nullable", "ordinal", "selected"):
        assert columns_table.c[required].nullable is False, required
    assert columns_table.c.description.nullable is True


def test_embeddings_columns_and_cascades(embeddings_table: Any) -> None:
    expected = {"id", "connection_id", "table_id", "column_id", "content", "embedding"}
    assert expected <= set(embeddings_table.columns.keys())
    targets = {
        "connection_id": "app.connections.id",
        "table_id": "catalog.tables.id",
        "column_id": "catalog.columns.id",
    }
    for column, target in targets.items():
        foreign_key = _fk(embeddings_table, column)
        assert foreign_key.target_fullname == target, column
        assert foreign_key.ondelete == "CASCADE", column
    assert embeddings_table.c.column_id.nullable is True  # NULL: a table-level entry
    assert embeddings_table.c.embedding.nullable is False
    assert embeddings_table.c.content.nullable is False


def test_embedding_column_is_a_pgvector_type_without_a_fixed_dimension(
    embeddings_table: Any,
) -> None:
    from sqlalchemy.dialects import postgresql

    column_type: Any = embeddings_table.c.embedding.type

    compiled = str(column_type.compile(dialect=postgresql.dialect()))

    assert compiled.upper() == "VECTOR"
