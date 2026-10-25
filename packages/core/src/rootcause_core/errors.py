"""Exception hierarchy shared by every RootCause distribution."""


class RootCauseError(Exception):
    """Base class for all errors raised by RootCause code.

    ``str(exc)`` is for server-side logs only. What a caller may see is
    ``public_message``, ``code`` and ``status_code``; subclasses opt in to more.
    """

    public_message: str = "The request could not be processed."
    code: str = "rootcause_error"
    status_code: int = 500


class ClientError(RootCauseError):
    """A caller-caused failure whose message is safe to show to the caller (HTTP 400)."""

    code = "client_error"
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.public_message = message


class ConfigurationError(RootCauseError):
    """Raised when settings cannot be loaded or validated.

    The message names the offending ``ROOTCAUSE_*`` environment variables but
    never echoes their values, so it is safe to surface in logs and tracebacks.
    """

    def __init__(self, message: str, *, variables: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.variables = variables
