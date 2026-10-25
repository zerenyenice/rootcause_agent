"""``rootcause-db`` command line: upgrade, downgrade, current, revision, bootstrap.

Errors are reported as one line on stderr with a non-zero exit code. Messages
never include the database URL, host or password: SQLAlchemy and driver
messages can embed them, so only the error class and SQLSTATE are printed.
"""

import argparse
import sys
from collections.abc import Sequence

from alembic.util import CommandError
from sqlalchemy.exc import SQLAlchemyError

from rootcause_core.db.engine import SYNC_DRIVER, normalise_url
from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import ENVIRONMENT_VARIABLE, Environment, Settings, get_settings
from rootcause_db import migrate
from rootcause_db.bootstrap import bootstrap
from rootcause_db.errors import BootstrapRequiredError, ProdRefusalError

PROG = "rootcause-db"
EXIT_FAILURE = 1
PROD_REFUSED_COMMANDS = frozenset({"downgrade", "revision"})
SAFE_MESSAGE_ERRORS = (ConfigurationError, BootstrapRequiredError, ProdRefusalError, CommandError)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="Manage the application database.")
    sub = parser.add_subparsers(dest="command", required=True)

    upgrade = sub.add_parser("upgrade", help="Upgrade to a revision (default: head).")
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
        help="As a superuser: create the vector extension, schemas and app-role grants "
        "(idempotent).",
    )
    boot.add_argument(
        "--admin-url", required=True, help="Superuser URL of the application database."
    )
    boot.add_argument(
        "--app-role",
        default=None,
        help="Role to grant schema privileges to (default: the user of ROOTCAUSE_DATABASE_URL).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        _run(args)
    except SAFE_MESSAGE_ERRORS as error:
        raise _failure(str(error)) from None
    except SQLAlchemyError as error:
        raise _failure(_describe_database_error(error)) from None


def _run(args: argparse.Namespace) -> None:
    if args.command == "bootstrap":
        _bootstrap(args.admin_url, args.app_role)
        return
    settings = get_settings()
    _refuse_destructive_in_prod(args.command, settings)
    url = settings.database_url.get_secret_value()
    # Called through the module so the functions can be substituted in tests.
    if args.command == "upgrade":
        migrate.upgrade(url, args.revision)
    elif args.command == "downgrade":
        migrate.downgrade(url, args.revision)
    elif args.command == "current":
        migrate.current(url)
    else:
        migrate.revision(url, args.message, args.autogenerate)


def _refuse_destructive_in_prod(command: str, settings: Settings) -> None:
    if command in PROD_REFUSED_COMMANDS and settings.environment is Environment.PROD:
        raise ProdRefusalError(
            f"'{PROG} {command}' is refused in the {Environment.PROD.value} environment "
            f"({ENVIRONMENT_VARIABLE}={Environment.PROD.value})."
        )


def _bootstrap(admin_url: str, app_role: str | None) -> None:
    role = app_role if app_role is not None else _app_role_from_settings(admin_url)
    bootstrap(admin_url, app_role=role)


def _app_role_from_settings(admin_url: str) -> str | None:
    """User of ROOTCAUSE_DATABASE_URL, or None when it is the admin user itself."""
    app_user = normalise_url(get_settings().database_url.get_secret_value(), SYNC_DRIVER).username
    admin_user = normalise_url(admin_url, SYNC_DRIVER).username
    return None if app_user == admin_user else app_user


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
