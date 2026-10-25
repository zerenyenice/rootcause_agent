"""``rootcause-db`` command line: upgrade, downgrade, current, revision."""

import argparse
from collections.abc import Sequence

from rootcause_core.db import migrate
from rootcause_core.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rootcause-db", description="Manage database migrations.")
    sub = parser.add_subparsers(dest="command", required=True)

    upgrade = sub.add_parser("upgrade", help="Upgrade to a revision (default: head).")
    upgrade.add_argument("revision", nargs="?", default="head")

    downgrade = sub.add_parser("downgrade", help="Downgrade to a revision (e.g. base or -1).")
    downgrade.add_argument("revision")

    sub.add_parser("current", help="Show the current revision.")

    revision = sub.add_parser("revision", help="Create a new migration script.")
    revision.add_argument("-m", "--message", required=True)
    revision.add_argument("--autogenerate", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    url = get_settings().database_url.get_secret_value()
    if args.command == "upgrade":
        migrate.upgrade(url, args.revision)
    elif args.command == "downgrade":
        migrate.downgrade(url, args.revision)
    elif args.command == "current":
        migrate.current(url)
    else:
        migrate.revision(url, args.message, args.autogenerate)
