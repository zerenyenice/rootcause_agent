"""Process-wide configuration loaded from ``ROOTCAUSE_*`` environment variables.

Settings are read once and cached by :func:`get_settings`. Validation failures
are re-raised as :class:`~rootcause_core.errors.ConfigurationError` so callers
get a message that names the offending variables without leaking their values.
"""

import os
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, AnyUrl, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from rootcause_core.errors import ConfigurationError

ENV_PREFIX = "ROOTCAUSE_"
ENVIRONMENT_VARIABLE = f"{ENV_PREFIX}ENVIRONMENT"
DOTENV_FILE = ".env"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# (environment variable name, pydantic error type) pairs extracted from a
# ValidationError; deliberately value-free so they can be surfaced anywhere.
ValidationFailures = tuple[tuple[str, str], ...]


class Environment(StrEnum):
    """Deployment environment the process runs in."""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class LogFormat(StrEnum):
    """Rendering mode for structured log lines."""

    JSON = "json"
    CONSOLE = "console"


class OtelExporter(StrEnum):
    """Span exporter wired into the tracer provider."""

    NONE = "none"
    CONSOLE = "console"
    OTLP = "otlp"


class Settings(BaseSettings):
    """Immutable, validated runtime configuration.

    Values come from ``ROOTCAUSE_*`` environment variables. A ``.env`` file in
    the working directory is read as a fallback in ``dev`` and ``test`` only;
    when ``ROOTCAUSE_ENVIRONMENT`` is ``prod`` (checked by :func:`get_settings`)
    the file is ignored so production configuration always comes from the real
    environment.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=DOTENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        frozen=True,
        extra="ignore",
    )

    environment: Environment = Environment.DEV
    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.JSON
    database_url: SecretStr = Field(min_length=1)
    openviking_url: AnyUrl | None = None
    otel_exporter: OtelExporter = OtelExporter.NONE
    otel_endpoint: AnyHttpUrl | None = None
    service_name: str = Field(default="rootcause", min_length=1)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Accept ``info``/``Info``/``INFO`` alike; the literal check runs after."""
        return value.upper() if isinstance(value, str) else value


def _variable_name(location: tuple[int | str, ...]) -> str:
    if not location:
        return f"{ENV_PREFIX}<settings>"
    return f"{ENV_PREFIX}{str(location[0]).upper()}"


def _validation_failures(error: ValidationError) -> ValidationFailures:
    """Reduce a ValidationError to (variable name, error type) pairs, no values."""
    return tuple(
        (_variable_name(detail["loc"]), detail["type"])
        for detail in error.errors(include_url=False)
    )


def _to_configuration_error(failures: ValidationFailures) -> ConfigurationError:
    """Build a value-free error that lists each offending variable and why."""
    reasons = dict(failures)
    listing = ", ".join(f"{name} ({reason})" for name, reason in reasons.items())
    message = f"Invalid configuration for environment variable(s): {listing}."
    return ConfigurationError(message, variables=tuple(reasons))


def _is_prod_environment() -> bool:
    """Mirror pydantic-settings' case-insensitive lookup of the environment variable."""
    for name, value in os.environ.items():
        if name.upper() == ENVIRONMENT_VARIABLE:
            return value.strip().lower() == Environment.PROD.value
    return False


def _load_settings() -> Settings:
    if _is_prod_environment():
        return Settings(_env_file=None)
    return Settings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache the process settings.

    The ``.env`` fallback is disabled when ``ROOTCAUSE_ENVIRONMENT`` is ``prod``.

    Raises:
        ConfigurationError: when required variables are missing or invalid.
    """
    try:
        return _load_settings()
    except ValidationError as error:
        # Only names and error types leave this block. The ValidationError itself
        # embeds input values (including secrets), so it must not travel on the
        # exception chain; raising after the handler leaves both ``__cause__``
        # and ``__context__`` unset.
        failures = _validation_failures(error)
    raise _to_configuration_error(failures)


def clear_settings_cache() -> None:
    """Forget the cached settings so the next :func:`get_settings` reloads."""
    get_settings.cache_clear()
