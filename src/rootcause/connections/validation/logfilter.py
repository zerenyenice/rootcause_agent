"""Log hygiene for sqlglot: keep raw SQL text out of its log records."""

from __future__ import annotations

import logging
from typing import Final

_FALLBACK_MARKER: Final = "Falling back to parsing as a 'Command'"


class CommandFallbackFilter(logging.Filter):
    """Drop sqlglot's Command-fallback warning, which embeds raw SQL text."""

    def filter(self, record: logging.LogRecord) -> bool:
        return _FALLBACK_MARKER not in record.getMessage()


def install_sqlglot_log_filter() -> None:
    """Attach :class:`CommandFallbackFilter` to the ``sqlglot`` logger, once.

    Idempotent: repeated calls (module reloads, tests) never stack duplicate filters.
    """
    sqlglot_logger = logging.getLogger("sqlglot")
    if not any(isinstance(item, CommandFallbackFilter) for item in sqlglot_logger.filters):
        sqlglot_logger.addFilter(CommandFallbackFilter())
