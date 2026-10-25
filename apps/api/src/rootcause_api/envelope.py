"""Uniform response envelope shared by every API route."""

from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel

REQUEST_ID_HEADER = "X-Request-ID"


class ErrorBody(BaseModel):
    """Machine-readable error code, safe message and optional structured details."""

    code: str
    message: str
    details: dict[str, Any] | None = None


class Meta(BaseModel):
    """Response metadata: request correlation id and optional pagination."""

    request_id: str | None = None
    total: int | None = None
    page: int | None = None
    limit: int | None = None


class Envelope[T](BaseModel):
    """``success`` flag plus either ``data`` or ``error``, with optional ``meta``."""

    success: bool
    data: T | None = None
    error: ErrorBody | None = None
    meta: Meta | None = None


def ok[T](data: T, meta: Meta | None = None) -> Envelope[T]:
    """Wrap ``data`` in a successful envelope."""
    return Envelope[T](success=True, data=data, meta=meta)


def fail(
    code: str,
    message: str,
    status: int,
    *,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build an error envelope response carrying the request id when known."""
    envelope: Envelope[None] = Envelope(
        success=False,
        error=ErrorBody(code=code, message=message, details=details),
        meta=Meta(request_id=request_id) if request_id else None,
    )
    response_headers = dict(headers or {})
    if request_id:
        response_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status,
        content=envelope.model_dump(mode="json"),
        headers=response_headers,
    )
