"""Security response headers shared by the middleware and the exception handlers."""

from starlette.responses import Response

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
HSTS_HEADER = "Strict-Transport-Security"
HSTS_VALUE = "max-age=31536000; includeSubDomains"


def security_headers(*, hsts: bool) -> dict[str, str]:
    """Base headers, plus HSTS when ``hsts`` (prod) is set."""
    return {**SECURITY_HEADERS, **({HSTS_HEADER: HSTS_VALUE} if hsts else {})}


def apply_security_headers(response: Response, *, hsts: bool) -> Response:
    """Set the security headers on ``response`` in place and return it."""
    for name, value in security_headers(hsts=hsts).items():
        response.headers[name] = value
    return response
