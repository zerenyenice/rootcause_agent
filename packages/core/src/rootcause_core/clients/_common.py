"""Helpers shared by the client factories."""

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import ENV_PREFIX


def _require[T](value: T | None, variable: str) -> T:
    """Return ``value`` or raise a value-free error naming ``ROOTCAUSE_<variable>``.

    Raises:
        ConfigurationError: ``value`` is ``None``.
    """
    if value is None:
        name = f"{ENV_PREFIX}{variable}"
        raise ConfigurationError(
            f"{name} is required for the selected provider.", variables=(name,)
        )
    return value
