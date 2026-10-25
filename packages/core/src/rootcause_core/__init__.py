"""RootCause shared core: settings, logging and base types."""

from rootcause_core.errors import ConfigurationError, RootCauseError
from rootcause_core.logging import configure_logging, get_logger
from rootcause_core.settings import (
    Environment,
    LogFormat,
    OtelExporter,
    Settings,
    clear_settings_cache,
    get_settings,
)
from rootcause_core.telemetry import configure_telemetry, get_tracer, shutdown_telemetry

__version__ = "0.1.0"

__all__ = [
    "ConfigurationError",
    "Environment",
    "LogFormat",
    "OtelExporter",
    "RootCauseError",
    "Settings",
    "__version__",
    "clear_settings_cache",
    "configure_logging",
    "configure_telemetry",
    "get_logger",
    "get_settings",
    "get_tracer",
    "shutdown_telemetry",
]
