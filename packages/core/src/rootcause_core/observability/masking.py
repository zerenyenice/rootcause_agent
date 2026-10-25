"""Mask applied to every input, output and metadata value before it reaches Langfuse.

Rules (PROJECT.md section 7, T009 amendment):

* **Tool outputs are denied by default.** A tool message (LangChain
  ``ToolMessage`` or its dict form with ``role``/``type`` ``tool``) keeps its
  content only for tools whose output is known to be safe (``write_todos``,
  ``explain_sql``) and for the ``run_sql`` summary (columns, row count,
  truncation flag, SQL, result path). Every other tool output, including
  ``read_file``/``grep``/``ls`` over stored results, eviction previews and
  subagent ``task`` results, becomes ``<redacted tool output: NAME>``. The tool
  name comes from the message itself or from the ``tool_calls`` of an assistant
  message in the same list, so generation inputs echoing tool results are
  covered as well.
* **Stored results are dropped.** Mapping entries keyed by a path under
  ``/query_results/`` or ``/large_tool_results/`` (agent filesystem state) are
  removed.
* **SQL result payloads lose their rows** wherever they appear (a mapping with
  both ``columns`` and ``rows``).
* **Every string is scrubbed**: credentials via the log redactor, then, when
  enabled, the PII pass (:mod:`rootcause_core.observability.pii`).

The mask reaches into JSON text and into LangChain messages, other pydantic
models and dataclasses (``langgraph.types.Command``). It never mutates its input
and always returns JSON-serialisable data.
"""

import dataclasses
import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final

from pydantic import BaseModel

from rootcause_core.logging import make_text_redactor
from rootcause_core.observability.pii import redact_pii

ROWS_KEY: Final = "rows"
COLUMNS_KEY: Final = "columns"
ROW_COUNT_KEY: Final = "row_count"
CONTENT_KEY: Final = "content"
# Tool message fields that may carry tool output besides ``content``.
TOOL_PAYLOAD_KEYS: Final = frozenset({CONTENT_KEY, "artifact"})
TOOL_MARKERS: Final = frozenset({"tool", "tool_result"})
TOOL_OUTPUT_MARKER: Final = "<redacted tool output: {name}>"
UNKNOWN_TOOL: Final = "unknown"
# Tools whose output carries no customer data.
KEPT_TOOL_OUTPUTS: Final = frozenset({"write_todos", "explain_sql"})
SQL_SUMMARY_TOOL: Final = "run_sql"
SQL_SUMMARY_KEYS: Final = frozenset(
    {
        "columns",
        "row_count",
        "truncated",
        "sql",
        "path",
        "approval_required",
        "estimated_rows",
        "threshold",
        "error",
    }
)
RESULT_PATH_PREFIXES: Final = ("/query_results/", "/large_tool_results/")
# Guards against self-referencing structures; real payloads are far shallower.
MAX_DEPTH: Final = 64
DEPTH_EXCEEDED: Final = "<max depth exceeded>"
JSON_OPENERS: Final = ("{", "[")

Scrub = Callable[[str], str]
MaskFunction = Callable[..., Any]
ToolNames = Mapping[str, str]


def build_scrubber(secrets: Iterable[str] = (), *, mask_pii: bool = True) -> Scrub:
    """Return a string scrubber: credential redaction, then the optional PII pass."""
    redact = make_text_redactor(tuple(secrets))
    if not mask_pii:
        return redact

    def scrub(text: str) -> str:
        return redact_pii(redact(text))

    return scrub


def _redacted(name: str) -> str:
    return TOOL_OUTPUT_MARKER.format(name=name)


def _is_result_path(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(RESULT_PATH_PREFIXES)


def _is_tool_message(value: Mapping[Any, Any]) -> bool:
    return value.get("role") in TOOL_MARKERS or value.get("type") in TOOL_MARKERS


def _as_plain(value: Any) -> Any:
    """One level of model/dataclass unwrapping (shallow: no deep copy of payloads)."""
    if isinstance(value, BaseModel):
        try:
            return value.model_dump()
        except Exception:  # noqa: BLE001 - an unserialisable model is masked as text
            return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
    return value


def _call_name(call: Any) -> tuple[str, str] | None:
    if not isinstance(call, Mapping):
        return None
    function = call.get("function")
    name = call.get("name") or (function.get("name") if isinstance(function, Mapping) else None)
    call_id = call.get("id")
    if isinstance(name, str) and isinstance(call_id, str):
        return call_id, name
    return None


def _tool_names(items: list[Any]) -> dict[str, str]:
    """Map tool call ids to tool names from the assistant messages in ``items``."""
    names: dict[str, str] = {}
    for item in items:
        calls = item.get("tool_calls") if isinstance(item, Mapping) else None
        if not isinstance(calls, list):
            continue
        names.update(pair for pair in map(_call_name, calls) if pair is not None)
    return names


def _is_sql_result(value: Mapping[Any, Any]) -> bool:
    return COLUMNS_KEY in value and ROWS_KEY in value


def _without_rows(value: Mapping[Any, Any]) -> dict[Any, Any]:
    reduced = {key: item for key, item in value.items() if key != ROWS_KEY}
    rows = value[ROWS_KEY]
    if ROW_COUNT_KEY not in reduced and isinstance(rows, list | tuple):
        reduced[ROW_COUNT_KEY] = len(rows)
    return reduced


@dataclasses.dataclass(frozen=True)
class _Masker:
    scrub: Scrub

    def mask(self, value: Any, depth: int, names: ToolNames) -> Any:
        if depth > MAX_DEPTH:
            return DEPTH_EXCEEDED
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            return self._text(value, depth, names)
        if isinstance(value, Mapping):
            return self._mapping(value, depth, names)
        if isinstance(value, list | tuple | set | frozenset):
            return self._sequence(list(value), depth, names)
        plain = _as_plain(value)
        if plain is value:
            return self.scrub(str(value))
        return self.mask(plain, depth + 1, names)

    def _text(self, text: str, depth: int, names: ToolNames) -> str:
        if not text.lstrip().startswith(JSON_OPENERS):
            return self.scrub(text)
        try:
            parsed = json.loads(text)
        except ValueError:
            return self.scrub(text)
        if not isinstance(parsed, dict | list):
            return self.scrub(text)
        return json.dumps(self.mask(parsed, depth + 1, names), ensure_ascii=False)

    def _sequence(self, items: list[Any], depth: int, names: ToolNames) -> list[Any]:
        plain = [_as_plain(item) for item in items]
        scoped = {**names, **_tool_names(plain)}
        return [self.mask(item, depth + 1, scoped) for item in plain]

    def _mapping(self, value: Mapping[Any, Any], depth: int, names: ToolNames) -> dict[str, Any]:
        if _is_tool_message(value):
            return self._tool_message(value, depth, names)
        source = _without_rows(value) if _is_sql_result(value) else value
        return {
            str(key): self.mask(item, depth + 1, names)
            for key, item in source.items()
            if not _is_result_path(key)
        }

    def _tool_message(
        self, value: Mapping[Any, Any], depth: int, names: ToolNames
    ) -> dict[str, Any]:
        call_id = value.get("tool_call_id") or value.get("tool_use_id")
        name = value.get("name") or (names.get(call_id) if isinstance(call_id, str) else None)
        resolved = name if isinstance(name, str) and name else UNKNOWN_TOOL
        kept = {
            str(key): self.mask(item, depth + 1, names)
            for key, item in value.items()
            if key not in TOOL_PAYLOAD_KEYS
        }
        return {**kept, CONTENT_KEY: self._tool_content(resolved, value.get(CONTENT_KEY), depth)}

    def _tool_content(self, name: str, content: Any, depth: int) -> Any:
        if name in KEPT_TOOL_OUTPUTS:
            return self.mask(content, depth + 1, {})
        if name == SQL_SUMMARY_TOOL:
            return self._sql_summary(content, depth)
        return _redacted(name)

    def _sql_summary(self, content: Any, depth: int) -> Any:
        parsed = content
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except ValueError:
                return _redacted(SQL_SUMMARY_TOOL)
        if not isinstance(parsed, Mapping):
            return _redacted(SQL_SUMMARY_TOOL)
        summary = {key: item for key, item in parsed.items() if key in SQL_SUMMARY_KEYS}
        if ROW_COUNT_KEY not in summary and isinstance(parsed.get(ROWS_KEY), list):
            summary[ROW_COUNT_KEY] = len(parsed[ROWS_KEY])
        masked = self.mask(summary, depth + 1, {})
        return json.dumps(masked, ensure_ascii=False) if isinstance(content, str) else masked


def build_mask(secrets: Iterable[str] = (), *, mask_pii: bool = True) -> MaskFunction:
    """Return a Langfuse ``mask(*, data, **kwargs)`` applying the rules in the module doc."""
    masker = _Masker(scrub=build_scrubber(secrets, mask_pii=mask_pii))

    def mask(*, data: Any, **_kwargs: Any) -> Any:
        return masker.mask(data, 0, {})

    return mask
