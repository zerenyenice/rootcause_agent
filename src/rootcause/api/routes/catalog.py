"""``/api/v1/catalog``: import, list and edit a connection's metadata catalog; build its index.

Imports introspect the source in a worker thread and upsert the catalog (ids,
selections and descriptions survive; see ``rootcause.catalog.import_plan``). The
index is built synchronously (``done`` or ``failed``). Responses never carry
source paths, hosts or provider errors.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from langchain_core.embeddings import Embeddings
from pydantic import ValidationError

from rootcause.api.catalog_schemas import (
    CatalogColumnOut,
    CatalogPatch,
    CatalogTableOut,
    ImportOut,
    IndexStatusOut,
)
from rootcause.api.envelope import Envelope, Meta, ok
from rootcause.api.middleware import request_id_of
from rootcause.api.routes.connections import get_connection_repository
from rootcause.api.routes.health import AppSettings
from rootcause.catalog.repository import CatalogRepository, SqlCatalogRepository
from rootcause.catalog.service import (
    build_index,
    import_catalog,
    index_status,
    require_connection,
)
from rootcause.clients.embeddings import embeddings
from rootcause.connections.repository import ConnectionRepository
from rootcause.db.engine import make_async_engine
from rootcause.errors import RootCauseError
from rootcause.logging import get_logger

PREFIX = "/api/v1/catalog"
_REPOSITORY_STATE = "catalog_repository"

_logger = get_logger(__name__)
router = APIRouter(prefix=PREFIX, tags=["catalog"])


class CatalogEntryNotFound(RootCauseError):
    code = "catalog_entry_not_found"
    status_code = 404
    public_message = "Catalog table or column not found."

    def __init__(self) -> None:
        super().__init__("Catalog table or column not found.")


async def get_catalog_repository(request: Request) -> CatalogRepository:
    """The app's SQL catalog repository, built on first use."""
    state = request.app.state
    repository: CatalogRepository | None = getattr(state, _REPOSITORY_STATE, None)
    if repository is None:
        repository = SqlCatalogRepository(make_async_engine(state.settings))
        setattr(state, _REPOSITORY_STATE, repository)
    return repository


async def close_catalog_repository(app: FastAPI) -> None:
    """Dispose the SQL repository's engine, if one was built (app shutdown)."""
    repository = getattr(app.state, _REPOSITORY_STATE, None)
    if isinstance(repository, SqlCatalogRepository):
        await repository.dispose()


def get_embeddings(settings: AppSettings) -> Embeddings:
    """The configured embeddings client (``ConfigurationError`` when it is not configured)."""
    return embeddings(settings)


Catalog = Annotated[CatalogRepository, Depends(get_catalog_repository)]
Connections = Annotated[ConnectionRepository, Depends(get_connection_repository)]
Embedder = Annotated[Embeddings, Depends(get_embeddings)]


def _meta(request: Request, total: int | None = None) -> Meta:
    return Meta(request_id=request_id_of(request), total=total)


def _patch(changes: dict[str, Any]) -> CatalogPatch:
    """Validate a PATCH body (422 on failure, without echoing the submitted values)."""
    try:
        return CatalogPatch.model_validate(changes)
    except ValidationError as error:
        errors = error.errors(include_url=False, include_input=False, include_context=False)
    # Raised outside the handler so pydantic's error (which holds the input) is not chained.
    raise RequestValidationError([{**item, "loc": ("body", *item["loc"])} for item in errors])


@router.post("/{connection_id}/import", response_model=Envelope[ImportOut])
async def import_connection_catalog(
    request: Request,
    connection_id: uuid.UUID,
    connections: Connections,
    catalog: Catalog,
    settings: AppSettings,
) -> Envelope[ImportOut]:
    counts = await import_catalog(
        connection_id, connections=connections, catalog=catalog, settings=settings
    )
    return ok(ImportOut.of(counts), _meta(request))


@router.get(
    "/{connection_id}/tables",
    response_model=Envelope[list[CatalogTableOut]],
    response_model_by_alias=True,
)
async def list_catalog_tables(
    request: Request, connection_id: uuid.UUID, connections: Connections, catalog: Catalog
) -> Envelope[list[CatalogTableOut]]:
    await require_connection(connection_id, connections)
    tables = await catalog.list_tables(connection_id)
    return ok([CatalogTableOut.of(table) for table in tables], _meta(request, len(tables)))


@router.patch(
    "/tables/{table_id}", response_model=Envelope[CatalogTableOut], response_model_by_alias=True
)
async def update_catalog_table(
    request: Request,
    table_id: uuid.UUID,
    changes: Annotated[dict[str, Any], Body()],
    catalog: Catalog,
) -> Envelope[CatalogTableOut]:
    values = _patch(changes).changes()
    updated = await catalog.update_table(table_id, values)
    if updated is None:
        raise CatalogEntryNotFound()
    _logger.info("catalog_table_updated", table_id=str(table_id), fields=sorted(values))
    return ok(CatalogTableOut.of(updated), _meta(request))


@router.patch("/columns/{column_id}", response_model=Envelope[CatalogColumnOut])
async def update_catalog_column(
    request: Request,
    column_id: uuid.UUID,
    changes: Annotated[dict[str, Any], Body()],
    catalog: Catalog,
) -> Envelope[CatalogColumnOut]:
    values = _patch(changes).changes()
    updated = await catalog.update_column(column_id, values)
    if updated is None:
        raise CatalogEntryNotFound()
    _logger.info("catalog_column_updated", column_id=str(column_id), fields=sorted(values))
    return ok(CatalogColumnOut.of(updated), _meta(request))


@router.post("/{connection_id}/index", response_model=Envelope[IndexStatusOut])
async def build_connection_index(
    request: Request,
    connection_id: uuid.UUID,
    connections: Connections,
    catalog: Catalog,
    embedder: Embedder,
    settings: AppSettings,
) -> Envelope[IndexStatusOut]:
    """Embed the selected catalog synchronously; ``failed`` keeps the previous index."""
    status = await build_index(
        connection_id,
        connections=connections,
        catalog=catalog,
        embedder=embedder,
        settings=settings,
    )
    return ok(IndexStatusOut.of(status), _meta(request))


@router.get("/{connection_id}/index", response_model=Envelope[IndexStatusOut])
async def get_connection_index(
    request: Request, connection_id: uuid.UUID, connections: Connections, catalog: Catalog
) -> Envelope[IndexStatusOut]:
    status = await index_status(connection_id, connections=connections, catalog=catalog)
    return ok(IndexStatusOut.of(status), _meta(request))
