"""Environment guards for destructive migration steps."""

import os

from rootcause_core.settings import ENVIRONMENT_VARIABLE, Environment
from rootcause_db.errors import ProdRefusalError


def is_prod_process_environment() -> bool:
    """True when ``ROOTCAUSE_ENVIRONMENT`` in the process environment is ``prod``.

    Reads the variable directly (case-insensitive name, like the settings
    loader) so that a migration script can check it without requiring a full,
    valid settings object.
    """
    for name, value in os.environ.items():
        if name.upper() == ENVIRONMENT_VARIABLE:
            return value.strip().lower() == Environment.PROD.value
    return False


def refuse_in_prod(action: str) -> None:
    """Raise :class:`ProdRefusalError` for ``action`` when running in prod."""
    if is_prod_process_environment():
        raise ProdRefusalError(
            f"{action} is refused in the {Environment.PROD.value} environment "
            f"({ENVIRONMENT_VARIABLE}={Environment.PROD.value})."
        )
