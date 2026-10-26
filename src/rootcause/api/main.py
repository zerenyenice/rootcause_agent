"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from limits import parse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import rootcause.api
from rootcause.api.envelope import REQUEST_ID_HEADER
from rootcause.api.errors import register_exception_handlers
from rootcause.api.middleware import (
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    create_limiter,
)
from rootcause.api.routes import catalog, connections, health
from rootcause.errors import ConfigurationError
from rootcause.logging import configure_logging, get_logger
from rootcause.settings import Environment, Settings, get_settings
from rootcause.telemetry import configure_telemetry

APP_TITLE = "RootCause API"
CORS_METHODS: Final = ("GET", "POST", "PATCH", "DELETE")
CORS_HEADERS: Final = ("Content-Type", "Accept", REQUEST_ID_HEADER)


def configure_telemetry_once(settings: Settings) -> None:
    """The tracer provider is set-once per process; later app builds reuse it."""
    try:
        configure_telemetry(settings)
    except ConfigurationError:
        get_logger(__name__).warning("telemetry_already_configured")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    try:
        await catalog.close_catalog_repository(app)
    finally:
        await connections.close_connection_repository(app)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application; ``settings`` defaults to the cached process settings."""
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved)
    configure_telemetry_once(resolved)

    is_prod = resolved.environment is Environment.PROD
    app = FastAPI(
        title=APP_TITLE,
        version=rootcause.api.__version__,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
        lifespan=_lifespan,
    )
    app.state.settings = resolved
    app.state.limiter = create_limiter()
    app.state.rate_limit_item = parse(resolved.rate_limit)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(connections.router)
    app.include_router(catalog.router)

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=is_prod)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(  # outermost: preflights and error responses carry CORS headers
        CORSMiddleware,
        allow_origins=list(resolved.api_allowed_origins),
        allow_methods=list(CORS_METHODS),
        allow_headers=list(CORS_HEADERS),
        allow_credentials=False,
    )
    FastAPIInstrumentor.instrument_app(app)
    return app
