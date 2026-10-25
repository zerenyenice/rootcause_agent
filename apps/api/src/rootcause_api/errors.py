"""Exception handlers: every error path answers with an :class:`Envelope`."""

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from rootcause_api.envelope import fail
from rootcause_api.middleware import request_id_of
from rootcause_core.errors import ConfigurationError, RootCauseError
from rootcause_core.logging import get_logger

logger = get_logger(__name__)

GENERIC_INTERNAL_MESSAGE = "An internal error occurred."
GENERIC_CONFIGURATION_MESSAGE = "The service is misconfigured."


def _reason_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


async def rootcause_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("rootcause_error", path=request.url.path, error=str(exc))
    return fail("rootcause_error", str(exc), 400, request_id=request_id_of(request))


async def configuration_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("configuration_error", path=request.url.path, error=str(exc))
    return fail(
        "configuration_error", GENERIC_CONFIGURATION_MESSAGE, 500, request_id=request_id_of(request)
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Field, message and type only: pydantic's ``input`` echo is deliberately dropped.
    errors = [
        {
            "field": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in exc.errors()
    ]
    logger.info("validation_error", path=request.url.path, fields=[e["field"] for e in errors])
    return fail(
        "validation_error",
        "Request validation failed.",
        422,
        details={"errors": errors},
        request_id=request_id_of(request),
    )


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    message = exc.detail if isinstance(exc.detail, str) else _reason_phrase(exc.status_code)
    logger.info("http_error", path=request.url.path, status=exc.status_code)
    return fail(
        "http_error",
        message,
        exc.status_code,
        request_id=request_id_of(request),
        headers=dict(exc.headers or {}),
    )


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "internal_error", path=request.url.path, error_type=type(exc).__name__, exc_info=exc
    )
    return fail("internal_error", GENERIC_INTERNAL_MESSAGE, 500, request_id=request_id_of(request))


def register_exception_handlers(app: FastAPI) -> None:
    """Install all envelope-producing handlers on ``app``."""
    app.add_exception_handler(ConfigurationError, configuration_error_handler)
    app.add_exception_handler(RootCauseError, rootcause_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)
