"""FastAPI application factory."""

from fastapi import FastAPI
from limits import parse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import rootcause.api
from rootcause.api.errors import register_exception_handlers
from rootcause.api.middleware import (
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    create_limiter,
)
from rootcause.api.routes import health
from rootcause.errors import ConfigurationError
from rootcause.logging import configure_logging, get_logger
from rootcause.settings import Environment, Settings, get_settings
from rootcause.telemetry import configure_telemetry

APP_TITLE = "RootCause API"


def _configure_telemetry_once(settings: Settings) -> None:
    """The tracer provider is set-once per process; later app builds reuse it."""
    try:
        configure_telemetry(settings)
    except ConfigurationError:
        get_logger(__name__).warning("telemetry_already_configured")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application; ``settings`` defaults to the cached process settings."""
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved)
    _configure_telemetry_once(resolved)

    is_prod = resolved.environment is Environment.PROD
    app = FastAPI(
        title=APP_TITLE,
        version=rootcause.api.__version__,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.state.settings = resolved
    app.state.limiter = create_limiter()
    app.state.rate_limit_item = parse(resolved.rate_limit)

    register_exception_handlers(app)
    app.include_router(health.router)

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=is_prod)
    app.add_middleware(RequestIdMiddleware)  # added last = outermost
    FastAPIInstrumentor.instrument_app(app)
    return app
