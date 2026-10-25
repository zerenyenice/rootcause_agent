"""Process-wide configuration loaded from ``ROOTCAUSE_*`` environment variables.

Settings are read once and cached by :func:`get_settings`. Validation failures
are re-raised as :class:`~rootcause_core.errors.ConfigurationError` so callers
get a message that names the offending variables without leaking their values.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import AnyUrl, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from rootcause_core.errors import ConfigurationError

ENV_PREFIX = "ROOTCAUSE_"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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
    """Immutable, validated runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
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
    otel_endpoint: str | None = None
    service_name: str = Field(default="rootcause", min_length=1)


def _variable_name(location: tuple[int | str, ...]) -> str:
    if not location:
        return f"{ENV_PREFIX}<settings>"
    return f"{ENV_PREFIX}{str(location[0]).upper()}"


def _to_configuration_error(error: ValidationError) -> ConfigurationError:
    """Build a value-free error that lists each offending variable and why."""
    reasons = {
        _variable_name(detail["loc"]): detail["type"] for detail in error.errors(include_url=False)
    }
    variables = tuple(reasons)
    listing = ", ".join(f"{name} ({reason})" for name, reason in reasons.items())
    message = f"Invalid configuration for environment variable(s): {listing}."
    return ConfigurationError(message, variables=variables)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache the process settings.

    Raises:
        ConfigurationError: when required variables are missing or invalid.
    """
    try:
        # Required fields are populated from the environment by pydantic-settings;
        # mypy only sees the dataclass-style signature, hence the targeted ignore.
        return Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        # ``from None`` keeps the raw ValidationError (which embeds input values,
        # including secrets) out of the exception chain and therefore out of logs.
        raise _to_configuration_error(error) from None


def clear_settings_cache() -> None:
    """Forget the cached settings so the next :func:`get_settings` reloads."""
    get_settings.cache_clear()
