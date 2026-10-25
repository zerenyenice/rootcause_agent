"""``rootcause-db`` command line: upgrade, downgrade, current, revision, bootstrap.

URLs (docs/adr/0007):

- migration commands connect as the migrator role, from
  ``ROOTCAUSE_MIGRATION_DATABASE_URL``;
- ``bootstrap`` connects as a superuser, from ``ROOTCAUSE_ADMIN_DATABASE_URL``
  or ``--admin-url`` (only without a password: PGPASSWORD or ``.pgpass``
  supply it, so it never appears in the process list or shell history).

Errors are reported as one line on stderr with a non-zero exit code. Messages
never include the database URL, host or password: SQLAlchemy and driver
messages can embed them, so only the error class and SQLSTATE are printed.
Anything unexpected prints ``unexpected error (<Type>)``; the global
``--debug`` flag re-raises instead, showing the traceback.
"""

import argparse
import sys
from collections.abc import Sequence

from alembic.util import CommandError
from sqlalchemy.exc import SQLAlchemyError

from rootcause_core.db.engine import SYNC_DRIVER, normalise_url
from rootcause_core.errors import ConfigurationError, RootCauseError
from rootcause_core.settings import ENVIRONMENT_VARIABLE, Environment, Settings, get_settings
from rootcause_db import migrate
from rootcause_db.bootstrap import bootstrap
from rootcause_db.errors import ProdRefusalError
from rootcause_db.names import (
    ADMIN_URL_OPTION,
    ADMIN_URL_VARIABLE,
    MIGRATION_URL_VARIABLE,
    PASSWORD_VARIABLE,
)

PROG = "rootcause-db"
EXIT_FAILURE = 1
PROD_REFUSED_COMMANDS = frozenset({"downgrade", "revision"})
SAFE_MESSAGE_ERRORS = (RootCauseError, CommandError)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="Manage the application database.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show the traceback of unexpected and database errors (may include the host).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    upgrade = sub.add_parser(
        "upgrade",
        help=f"Upgrade to a revision (default: head) as the migrator ({MIGRATION_URL_VARIABLE}).",
    )
    upgrade.add_argument("revision", nargs="?", default="head")

    downgrade = sub.add_parser(
        "downgrade", help="Downgrade to a revision (e.g. base or -1); refused in prod."
    )
    downgrade.add_argument("revision")

    sub.add_parser("current", help="Show the current revision.")

    revision = sub.add_parser("revision", help="Create a new migration script; refused in prod.")
    revision.add_argument("-m", "--message", required=True)
    revision.add_argument("--autogenerate", action="store_true")

    boot = sub.add_parser(
        "bootstrap",
        help="As a superuser: create the vector extension, owner-owned schemas and role grants "
        f"(idempotent). The superuser URL comes from {ADMIN_URL_VARIABLE}.",
    )
    boot.add_argument(
        ADMIN_URL_OPTION,
        default=None,
        help=f"Superuser URL WITHOUT a password (use {PASSWORD_VARIABLE} or .pgpass); "
        f"overrides {ADMIN_URL_VARIABLE}.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        _run(args)
    except SAFE_MESSAGE_ERRORS as error:
        raise _failure(str(error)) from None
    except SQLAlchemyError as error:
        if args.debug:
            raise
        raise _failure(_describe_database_error(error)) from None
    except Exception as error:
        if args.debug:
            raise
        raise _failure(
            f"unexpected error ({type(error).__name__}); rerun with --debug for the traceback."
        ) from None


def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    if args.command == "bootstrap":
        _bootstrap(settings, args.admin_url)
        return
    _refuse_destructive_in_prod(args.command, settings)
    url = _migration_url(settings)
    # Called through the module so the functions can be substituted in tests.
    if args.command == "upgrade":
        migrate.upgrade(url, args.revision)
    elif args.command == "downgrade":
        migrate.downgrade(url, args.revision)
    elif args.command == "current":
        migrate.current(url)
    else:
        migrate.revision(url, args.message, args.autogenerate)


def _migration_url(settings: Settings) -> str:
    if settings.migration_database_url is None:
        raise ConfigurationError(
            f"{MIGRATION_URL_VARIABLE} is not set; migrations connect as the migrator role.",
            variables=(MIGRATION_URL_VARIABLE,),
        )
    return settings.migration_database_url.get_secret_value()


def _refuse_destructive_in_prod(command: str, settings: Settings) -> None:
    if command in PROD_REFUSED_COMMANDS and settings.environment is Environment.PROD:
        raise ProdRefusalError(
            f"'{PROG} {command}' is refused in the {Environment.PROD.value} environment "
            f"({ENVIRONMENT_VARIABLE}={Environment.PROD.value})."
        )


def _bootstrap(settings: Settings, admin_url_option: str | None) -> None:
    bootstrap(
        _admin_url(settings, admin_url_option),
        owner_role=settings.db_owner_role,
        migrator_role=settings.db_migrator_role,
        app_role=settings.db_app_role,
    )


def _admin_url(settings: Settings, option: str | None) -> str:
    """Superuser URL from ``--admin-url`` (password-free) or ROOTCAUSE_ADMIN_DATABASE_URL."""
    if option is not None:
        if normalise_url(option, SYNC_DRIVER, source=ADMIN_URL_OPTION).password is not None:
            raise ConfigurationError(
                f"{ADMIN_URL_OPTION} must not contain a password (it would be visible in the "
                f"process list and shell history). Set {ADMIN_URL_VARIABLE} instead, or pass "
                f"the password via {PASSWORD_VARIABLE} or .pgpass.",
                variables=(ADMIN_URL_VARIABLE,),
            )
        return option
    if settings.admin_database_url is None:
        raise ConfigurationError(
            f"{ADMIN_URL_VARIABLE} is not set; bootstrap needs a superuser URL "
            f"(or {ADMIN_URL_OPTION} without a password plus {PASSWORD_VARIABLE}).",
            variables=(ADMIN_URL_VARIABLE,),
        )
    url = settings.admin_database_url.get_secret_value()
    normalise_url(url, SYNC_DRIVER, source=ADMIN_URL_VARIABLE)  # fail before connecting
    return url


def _describe_database_error(error: SQLAlchemyError) -> str:
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    detail = f", SQLSTATE {sqlstate}" if sqlstate else ""
    return (
        f"database operation failed ({type(error).__name__}{detail}). Check that the "
        "database is reachable and the credentials are valid; details are omitted because "
        "they can contain the connection URL."
    )


def _failure(message: str) -> SystemExit:
    print(f"{PROG}: error: {message}", file=sys.stderr)
    return SystemExit(EXIT_FAILURE)
