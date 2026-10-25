"""Request and response bodies of ``/api/v1/catalog`` (mirrors ``web/src/lib/api.ts``).

A PATCH body may carry ``selected`` (a strict boolean, never ``null``) and
``description`` (at most 2000 characters, no NUL; ``null`` or blank clears it).
Unknown fields are rejected, so source metadata (name, type, ...) is never editable.
"""

import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from rootcause.catalog.records import CatalogColumnRecord, CatalogCounts, CatalogTableRecord
from rootcause.catalog.service import IndexState, IndexStatus

MAX_DESCRIPTION_CHARS = 2000
_NO_NUL = r"^[^\x00]*$"

Description = Annotated[StrictStr, Field(max_length=MAX_DESCRIPTION_CHARS, pattern=_NO_NUL)]


class CatalogPatch(BaseModel):
    """Partial update of a catalog table or column; absent fields stay unchanged."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selected: StrictBool | None = None
    description: Description | None = None

    @field_validator("selected", mode="before")
    @classmethod
    def _selected_not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("selected may not be null")
        return value

    @field_validator("description", mode="after")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value if value is not None and value.strip() else None

    def changes(self) -> dict[str, Any]:
        """The fields the body set (``description`` possibly ``None``: cleared)."""
        return self.model_dump(include=self.model_fields_set)


class CatalogColumnOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    nullable: bool
    selected: bool
    description: str | None

    @classmethod
    def of(cls, column: CatalogColumnRecord) -> "CatalogColumnOut":
        return cls(
            id=column.id,
            name=column.name,
            type=column.data_type,
            nullable=column.nullable,
            selected=column.selected,
            description=column.description,
        )


class CatalogTableOut(BaseModel):
    id: uuid.UUID
    schema_: str = Field(serialization_alias="schema")
    name: str
    selected: bool
    description: str | None
    row_estimate: int | None
    columns: list[CatalogColumnOut]

    @classmethod
    def of(cls, table: CatalogTableRecord) -> "CatalogTableOut":
        return cls(
            id=table.id,
            schema_=table.schema,
            name=table.name,
            selected=table.selected,
            description=table.description,
            row_estimate=table.row_estimate,
            columns=[CatalogColumnOut.of(column) for column in table.columns],
        )


class ImportOut(BaseModel):
    tables: int
    columns: int

    @classmethod
    def of(cls, counts: CatalogCounts) -> "ImportOut":
        return cls(tables=counts.tables, columns=counts.columns)


class IndexStatusOut(BaseModel):
    status: IndexState
    indexed_tables: int
    indexed_columns: int

    @classmethod
    def of(cls, status: IndexStatus) -> "IndexStatusOut":
        return cls(
            status=status.status,
            indexed_tables=status.indexed_tables,
            indexed_columns=status.indexed_columns,
        )
