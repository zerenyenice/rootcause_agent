"""Thread registry: which thread ids this service has issued or persisted.

Ids minted by ``POST /threads`` are remembered in process; a thread that has run
at least once also has a checkpoint, so after a restart it is still found
through the checkpointer. A thread created but never run before a restart is
forgotten (the client simply creates a new one).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

THREAD_NAMESPACE: Final = ("rootcause", "threads")
TITLE_MAX_CHARS: Final = 80
# Upper bound of threads read to order them (a thread list is a short, recent list).
MAX_LISTED_THREADS: Final = 1000


@dataclass(frozen=True)
class ThreadSummary:
    """One row of ``POST /threads/search``."""

    thread_id: str
    created_at: datetime
    updated_at: datetime
    title: str | None
    connection_id: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": {"title": self.title, "connection_id": self.connection_id},
        }


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts = [
            block.get("text", "") if isinstance(block, Mapping) else str(block)
            for block in content
            if isinstance(block, str)
            or (isinstance(block, Mapping) and block.get("type") == "text")
        ]
        return " ".join(parts)
    return ""


def title_from_messages(messages: Sequence[Mapping[str, Any]]) -> str | None:
    """Single-line title (max 80 chars, ellipsis when cut) of the first human message."""
    for message in messages:
        if message.get("type", message.get("role")) not in ("human", "user"):
            continue
        text = " ".join(_text_of(message.get("content")).split())
        if not text:
            continue
        if len(text) > TITLE_MAX_CHARS:
            return text[: TITLE_MAX_CHARS - 1].rstrip() + "\u2026"
        return text
    return None


class ThreadRegistry:
    """Issues thread ids and answers whether a thread exists."""

    def __init__(self, checkpointer: BaseCheckpointSaver[Any], store: BaseStore) -> None:
        self._checkpointer = checkpointer
        self._store = store
        self._created: set[str] = set()

    def create(self) -> str:
        """Mint and remember a new UUID4 thread id."""
        thread_id = str(uuid.uuid4())
        self._created.add(thread_id)
        return thread_id

    async def exists(self, thread_id: str) -> bool:
        """True for an id issued by this process or one that has a checkpoint."""
        if thread_id in self._created:
            return True
        config = RunnableConfig(configurable={"thread_id": thread_id})
        return await self._checkpointer.aget_tuple(config) is not None

    async def record_run(
        self, thread_id: str, *, title: str | None, connection_id: str | None
    ) -> None:
        """Create or refresh the thread's metadata; ``updated_at`` bumps on every run.

        The title is fixed by the first run that has one; the connection follows the
        latest run that names one.
        """
        item = await self._store.aget(THREAD_NAMESPACE, thread_id)
        previous: Mapping[str, Any] = item.value if item is not None else {}
        value = {
            "title": previous.get("title") or title,
            "connection_id": connection_id or previous.get("connection_id"),
        }
        await self._store.aput(THREAD_NAMESPACE, thread_id, value, index=False)

    async def search(self, *, limit: int, offset: int) -> list[ThreadSummary]:
        """Threads that have run, newest ``updated_at`` first."""
        items = await self._store.asearch(THREAD_NAMESPACE, limit=MAX_LISTED_THREADS)
        ordered = sorted(items, key=lambda item: item.updated_at, reverse=True)
        return [
            ThreadSummary(
                thread_id=item.key,
                created_at=item.created_at,
                updated_at=item.updated_at,
                title=item.value.get("title"),
                connection_id=item.value.get("connection_id"),
            )
            for item in ordered[offset : offset + limit]
        ]

    async def delete(self, thread_id: str) -> None:
        """Remove the thread's metadata and every checkpoint (idempotent)."""
        self._created.discard(thread_id)
        await self._store.adelete(THREAD_NAMESPACE, thread_id)
        await self._checkpointer.adelete_thread(thread_id)
