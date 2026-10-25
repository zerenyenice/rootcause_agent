"""Exception hierarchy shared by every RootCause distribution."""


class RootCauseError(Exception):
    """Base class for all errors raised by RootCause code."""


class ConfigurationError(RootCauseError):
    """Raised when settings cannot be loaded or validated.

    The message names the offending ``ROOTCAUSE_*`` environment variables but
    never echoes their values, so it is safe to surface in logs and tracebacks.
    """

    def __init__(self, message: str, *, variables: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.variables = variables
