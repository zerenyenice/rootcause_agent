"""JSON and Server-Sent-Events encoding in the LangGraph Server wire format.

Messages are encoded as their ``model_dump()`` (``type`` ``ai``/``human``/
``AIMessageChunk`` ...), interrupts as ``{"value", "id"}`` and tuples as lists,
which is what the LangGraph SDK's ``useStream`` reads. Each frame is exactly
``event: <name>\\ndata: <json>\\n\\n``; JSON escapes every newline in a value, so
a frame never spans more lines.
"""

import dataclasses
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Final
from uuid import UUID

from langgraph.types import Interrupt
from pydantic import BaseModel

SSE_MEDIA_TYPE: Final = "text/event-stream"
UNSERIALIZABLE: Final = "<unserializable {name}>"


def _default(value: Any) -> Any:
    """``json.dumps`` fallback for the non-JSON types a graph state may hold."""
    if isinstance(value, Interrupt):
        return {"value": value.value, "id": value.id}
    if isinstance(value, BaseModel):
        return value.model_dump()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
    if isinstance(value, set | frozenset):
        return list(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    # Never ``str()``/``repr()`` an unknown object: it may render private data.
    return UNSERIALIZABLE.format(name=type(value).__name__)


def to_json(value: Any) -> str:
    """Compact JSON with LangGraph types encoded; non-ASCII text is kept as is."""
    return json.dumps(value, default=_default, ensure_ascii=False, separators=(",", ":"))


def sse_frame(event: str, data: Any) -> str:
    """One SSE frame: an ``event:`` line, a single ``data:`` line and a blank line."""
    return f"event: {event}\ndata: {to_json(data)}\n\n"
