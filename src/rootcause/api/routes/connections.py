"""``/api/v1/connections``: register, inspect, test and introspect customer data sources.

Passwords are encrypted with ``ROOTCAUSE_SECRET_KEY`` before they are stored and are
never returned or logged. ``POST /{id}/test`` records the outcome: a reachable,
read-only connection gets ``read_only_verified_at`` (ADR-0002); anything else clears it,
and so does any change to where or as whom the connection logs in. A probe whose login
changed while it ran records nothing (the change already cleared the verification).
"""

import uuid
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from rootcause.api.connection_schemas import (
    ConnectionOut,
    DeletedOut,
    DuckDBCreate,
    DuckDBPatch,
    IntrospectionOut,
    PostgresCreate,
    PostgresPatch,
    ProbeOut,
)
from rootcause.api.envelope import Envelope, Meta, ok
from rootcause.api.middleware import request_id_of
from rootcause.api.routes.health import AppSettings
from rootcause.connections.probe import introspect, probe
from rootcause.connections.repository import (
    VERIFIED_FIELD,
    ConnectionNotFound,
    ConnectionRecord,
    ConnectionRepository,
    NewConnection,
    SqlConnectionRepository,
)
from rootcause.connections.secrets import SecretCipher, cipher_from_settings
from rootcause.db.engine import make_async_engine
from rootcause.logging import get_logger
from rootcause.settings import Settings

PREFIX = "/api/v1/connections"
_REPOSITORY_STATE = "connection_repository"
# Changing any of these invalidates an earlier read-only verification.
_LOGIN_FIELDS = frozenset({"host", "port", "database", "user", "password", "sslmode", "path"})

_logger = get_logger(__name__)
router = APIRouter(prefix=PREFIX, tags=["connections"])


async def get_connection_repository(request: Request) -> ConnectionRepository:
    """The app's SQL repository, built on first use (async: no race between check and set)."""
    state = request.app.state
    repository: ConnectionRepository | None = getattr(state, _REPOSITORY_STATE, None)
    if repository is None:
        repository = SqlConnectionRepository(make_async_engine(state.settings))
        setattr(state, _REPOSITORY_STATE, repository)
    return repository


async def close_connection_repository(app: FastAPI) -> None:
    """Dispose the SQL repository's engine, if one was built (app shutdown)."""
    repository = getattr(app.state, _REPOSITORY_STATE, None)
    if isinstance(repository, SqlConnectionRepository):
        await repository.dispose()


Repository = Annotated[ConnectionRepository, Depends(get_connection_repository)]
ConnectionId = uuid.UUID


def _meta(request: Request, total: int | None = None) -> Meta:
    return Meta(request_id=request_id_of(request), total=total)


async def _require(repository: ConnectionRepository, connection_id: uuid.UUID) -> ConnectionRecord:
    record = await repository.get(connection_id)
    if record is None:
        raise ConnectionNotFound()
    return record


def _cipher_for(record: ConnectionRecord, settings: Settings) -> SecretCipher | None:
    return cipher_from_settings(settings) if record.kind == "postgres" else None


def _new_connection(body: PostgresCreate | DuckDBCreate, settings: Settings) -> NewConnection:
    if isinstance(body, DuckDBCreate):
        return NewConnection(name=body.name, kind="duckdb", path=body.path)
    secret = cipher_from_settings(settings).encrypt(body.password.get_secret_value())
    return NewConnection(
        name=body.name,
        kind="postgres",
        host=body.host,
        port=body.port,
        database=body.database,
        user=body.user,
        sslmode=body.sslmode,
        encrypted_secret=secret,
    )


def _parsed_patch(record: ConnectionRecord, changes: dict[str, Any]) -> PostgresPatch | DuckDBPatch:
    """Validate ``changes`` against the patch model of the record's kind (422 on failure)."""
    model = PostgresPatch if record.kind == "postgres" else DuckDBPatch
    try:
        return model.model_validate(changes)
    except ValidationError as error:
        errors = error.errors(include_url=False, include_input=False, include_context=False)
    # Raised outside the handler so pydantic's error (which holds the input) is not chained.
    raise RequestValidationError([{**item, "loc": ("body", *item["loc"])} for item in errors])


def _patch_values(patch: PostgresPatch | DuckDBPatch, settings: Settings) -> dict[str, Any]:
    values = patch.model_dump(exclude_unset=True)
    password = values.pop("password", None)
    if password is not None:
        values["encrypted_secret"] = cipher_from_settings(settings).encrypt(
            password.get_secret_value()
        )
    if _LOGIN_FIELDS & patch.model_fields_set:
        values[VERIFIED_FIELD] = None
    return values


@router.post("", status_code=201, response_model=Envelope[ConnectionOut])
async def create_connection(
    request: Request,
    body: Annotated[PostgresCreate | DuckDBCreate, Body(discriminator="kind")],
    repository: Repository,
    settings: AppSettings,
) -> Envelope[ConnectionOut]:
    record = await repository.create(_new_connection(body, settings))
    _logger.info("connection_created", connection_id=str(record.id), kind=record.kind)
    return ok(ConnectionOut.of(record), _meta(request))


@router.get("", response_model=Envelope[list[ConnectionOut]])
async def list_connections(
    request: Request, repository: Repository
) -> Envelope[list[ConnectionOut]]:
    records = await repository.list()
    return ok([ConnectionOut.of(record) for record in records], _meta(request, len(records)))


@router.get("/{connection_id}", response_model=Envelope[ConnectionOut])
async def get_connection(
    request: Request, connection_id: ConnectionId, repository: Repository
) -> Envelope[ConnectionOut]:
    return ok(ConnectionOut.of(await _require(repository, connection_id)), _meta(request))


@router.patch("/{connection_id}", response_model=Envelope[ConnectionOut])
async def update_connection(
    request: Request,
    connection_id: ConnectionId,
    changes: Annotated[dict[str, Any], Body()],
    repository: Repository,
    settings: AppSettings,
) -> Envelope[ConnectionOut]:
    record = await _require(repository, connection_id)
    values = _patch_values(_parsed_patch(record, changes), settings)
    updated = await repository.update(connection_id, values) if values else record
    if updated is None:
        raise ConnectionNotFound()
    _logger.info("connection_updated", connection_id=str(connection_id), fields=sorted(values))
    return ok(ConnectionOut.of(updated), _meta(request))


@router.delete("/{connection_id}", response_model=Envelope[DeletedOut])
async def delete_connection(
    request: Request, connection_id: ConnectionId, repository: Repository
) -> Envelope[DeletedOut]:
    if not await repository.delete(connection_id):
        raise ConnectionNotFound()
    _logger.info("connection_deleted", connection_id=str(connection_id))
    return ok(DeletedOut(id=connection_id, deleted=True), _meta(request))


@router.post("/{connection_id}/test", response_model=Envelope[ProbeOut])
async def test_connection(
    request: Request, connection_id: ConnectionId, repository: Repository, settings: AppSettings
) -> Envelope[ProbeOut]:
    """Reachability and read-only verification; always 200, the outcome is in the body."""
    record = await _require(repository, connection_id)
    outcome = await run_in_threadpool(probe, record, _cipher_for(record, settings))
    verified = outcome.reachable and outcome.read_only
    recorded = await repository.record_verification(record, datetime.now(UTC) if verified else None)
    _logger.info(
        "connection_tested",
        connection_id=str(connection_id),
        reachable=outcome.reachable,
        read_only=outcome.read_only,
        recorded=recorded,
    )
    return ok(ProbeOut.of(outcome), _meta(request))


@router.get("/{connection_id}/introspect", response_model=Envelope[IntrospectionOut])
async def introspect_connection(
    request: Request, connection_id: ConnectionId, repository: Repository, settings: AppSettings
) -> Envelope[IntrospectionOut]:
    """Schemas, tables (sorted, capped), row estimates and columns; 503 when unreachable."""
    record = await _require(repository, connection_id)
    introspection = await run_in_threadpool(
        partial(introspect, max_tables=settings.introspect_max_tables),
        record,
        _cipher_for(record, settings),
    )
    return ok(IntrospectionOut.of(introspection), _meta(request))
