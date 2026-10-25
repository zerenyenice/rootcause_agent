"""Thread registry: which thread ids this service has issued or persisted.

Ids minted by ``POST /threads`` are remembered in process; a thread that has run
at least once also has a checkpoint, so after a restart it is still found
through the checkpointer. A thread created but never run before a restart is
forgotten (the client simply creates a new one).
"""

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver


class ThreadRegistry:
    """Issues thread ids and answers whether a thread exists."""

    def __init__(self, checkpointer: BaseCheckpointSaver[Any]) -> None:
        self._checkpointer = checkpointer
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
