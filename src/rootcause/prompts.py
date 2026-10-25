"""Prompt registry: Langfuse-managed prompts with the packaged files as fallback.

With ``ROOTCAUSE_PROMPT_SOURCE=files`` (default) prompts come from the files
shipped with the code. With ``langfuse`` the version labelled
``ROOTCAUSE_PROMPT_LABEL`` is fetched (cached by the SDK for
``ROOTCAUSE_PROMPT_CACHE_TTL_S``); any Langfuse failure falls back to the file
and logs one warning per prompt name, so an outage never stops the agents.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from rootcause.clients.langfuse import fetch_prompt
from rootcause.logging import get_logger
from rootcause.settings import Settings, get_settings

PromptOrigin = Literal["files", "langfuse"]
FileLoader = Callable[[str], str]

_logger = get_logger(__name__)


@dataclass(frozen=True)
class PromptTemplate:
    """A resolved prompt; ``version`` is ``None`` for file prompts."""

    name: str
    version: int | None
    text: str
    source: PromptOrigin


class PromptRegistry:
    """Resolve prompts by name from Langfuse or the packaged files."""

    def __init__(self, *, settings: Settings | None = None, file_loader: FileLoader) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._file_loader = file_loader
        self._warned: set[str] = set()
        self._lock = threading.Lock()

    def get(self, name: str) -> PromptTemplate:
        """Return prompt ``name``; falls back to the file when Langfuse fails.

        Raises:
            Exception: whatever ``file_loader`` raises (e.g. FileNotFoundError).
        """
        if self._settings.prompt_source == "langfuse":
            remote = self._from_langfuse(name)
            if remote is not None:
                return remote
        return self._from_file(name)

    def _from_file(self, name: str) -> PromptTemplate:
        return PromptTemplate(name=name, version=None, text=self._file_loader(name), source="files")

    def _from_langfuse(self, name: str) -> PromptTemplate | None:
        try:
            prompt = fetch_prompt(name, self._settings)
        except Exception as error:  # noqa: BLE001 - any Langfuse failure falls back to files
            self._warn_fallback(name, error)
            return None
        return PromptTemplate(
            name=prompt.name, version=prompt.version, text=prompt.text, source="langfuse"
        )

    def _warn_fallback(self, name: str, error: Exception) -> None:
        with self._lock:
            if name in self._warned:
                return
            self._warned.add(name)
        # The error type only: SDK messages can carry hosts or response bodies.
        _logger.warning("prompt_fallback_to_file", prompt=name, error_type=type(error).__name__)
