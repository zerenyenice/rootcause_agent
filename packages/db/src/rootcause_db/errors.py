"""Errors raised by the migration tooling; their messages never contain a URL."""

from rootcause_core.errors import RootCauseError


class BootstrapRequiredError(RootCauseError):
    """The database lacks the extension, schemas or grants that ``bootstrap`` creates."""

    code = "bootstrap_required"


class ProdRefusalError(RootCauseError):
    """A destructive database operation was attempted in the prod environment."""

    code = "refused_in_prod"


class DatabaseConnectionError(RootCauseError):
    """The migration tooling could not connect; the message omits host, user and URL."""

    code = "database_unreachable"
