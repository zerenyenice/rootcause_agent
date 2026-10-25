"""Mask applied to every input, output and metadata value before it reaches Langfuse.

Two rules (PROJECT.md section 7):

* credentials are redacted from every string with the same redactor the log
  pipeline uses (URL passwords plus any verbatim secrets handed in);
* SQL result payloads (a mapping carrying both ``columns`` and ``rows``) lose
  their ``rows``; column names, ``row_count``, ``truncated`` and ``sql`` stay so
  a trace still shows what was queried and how much came back.

The mask reaches into JSON text (tool results are serialised as JSON strings)
and into LangChain messages and other pydantic models / dataclasses. It never
mutates its input and always returns JSON-serialisable data.
"""

import dataclasses
import json
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from rootcause_core.logging import make_text_redactor

ROWS_KEY = "rows"
COLUMNS_KEY = "columns"
ROW_COUNT_KEY = "row_count"
# Guards against self-referencing structures; real payloads are far shallower.
MAX_DEPTH = 64
DEPTH_EXCEEDED = "<max depth exceeded>"
JSON_OPENERS = ("{", "[")

Redact = Callable[[str], str]
MaskFunction = Callable[..., Any]


def _is_sql_result(value: Mapping[Any, Any]) -> bool:
    return COLUMNS_KEY in value and ROWS_KEY in value


def _without_rows(value: Mapping[Any, Any]) -> dict[Any, Any]:
    reduced = {key: item for key, item in value.items() if key != ROWS_KEY}
    rows = value[ROWS_KEY]
    if ROW_COUNT_KEY not in reduced and isinstance(rows, list | tuple):
        reduced[ROW_COUNT_KEY] = len(rows)
    return reduced


def _mask_mapping(value: Mapping[Any, Any], redact: Redact, depth: int) -> dict[str, Any]:
    source = _without_rows(value) if _is_sql_result(value) else value
    return {str(key): _mask(item, redact, depth + 1) for key, item in source.items()}


def _mask_text(text: str, redact: Redact, depth: int) -> str:
    if not text.lstrip().startswith(JSON_OPENERS):
        return redact(text)
    try:
        parsed = json.loads(text)
    except ValueError:
        return redact(text)
    if not isinstance(parsed, dict | list):
        return redact(text)
    return json.dumps(_mask(parsed, redact, depth + 1), ensure_ascii=False)


def _mask(value: Any, redact: Redact, depth: int) -> Any:
    if depth > MAX_DEPTH:
        return DEPTH_EXCEEDED
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _mask_text(value, redact, depth)
    if isinstance(value, Mapping):
        return _mask_mapping(value, redact, depth)
    if isinstance(value, list | tuple | set | frozenset):
        return [_mask(item, redact, depth + 1) for item in value]
    if isinstance(value, BaseModel):
        return _mask(value.model_dump(), redact, depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _mask(dataclasses.asdict(value), redact, depth + 1)
    return redact(str(value))


def build_mask(secrets: tuple[str, ...] = ()) -> MaskFunction:
    """Return a Langfuse ``mask(*, data, **kwargs)`` redacting ``secrets`` and SQL rows."""
    redact = make_text_redactor(secrets)

    def mask(*, data: Any, **_kwargs: Any) -> Any:
        return _mask(data, redact, 0)

    return mask
