"""Hide the model-written task text a DeepAgents subagent receives as its human message.

The ``task`` tool starts a subagent with ``HumanMessage(content=description)``,
and the description is model-written (it can carry copied query rows). Inside a
subagent run every human message is therefore replaced by a marker before the
run's inputs and outputs reach Langfuse; the parent run keeps the real user
question. Used by the hardened callback handler in
:mod:`rootcause_core.clients.langfuse`.
"""

import dataclasses
from collections.abc import Mapping
from typing import Any, Final

from langchain_core.messages import HumanMessage

TASK_TOOL: Final = "task"
SUBAGENT_TASK_MARKER: Final = "<redacted subagent task>"
HUMAN_MARKERS: Final = frozenset({"human", "user"})
# Run inputs are shallow (state -> messages -> message); deeper values are dropped.
MAX_DEPTH: Final = 32


def _is_human_dict(value: Mapping[Any, Any]) -> bool:
    return "content" in value and (
        value.get("role") in HUMAN_MARKERS or value.get("type") in HUMAN_MARKERS
    )


def without_human_text(value: Any, depth: int = 0) -> Any:
    """Return ``value`` with every human message's content replaced (never mutates)."""
    if depth > MAX_DEPTH:
        return SUBAGENT_TASK_MARKER
    if isinstance(value, HumanMessage):
        return value.model_copy(update={"content": SUBAGENT_TASK_MARKER})
    if isinstance(value, Mapping):
        if _is_human_dict(value):
            return {**value, "content": SUBAGENT_TASK_MARKER}
        return {key: without_human_text(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [without_human_text(item, depth + 1) for item in value]
    update = getattr(value, "update", None)
    if dataclasses.is_dataclass(value) and not isinstance(value, type) and update is not None:
        # A LangGraph ``Command`` (core does not depend on langgraph; matched by shape).
        return dataclasses.replace(value, update=without_human_text(update, depth + 1))
    return value
