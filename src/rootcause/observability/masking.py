"""Mask applied to every input, output and metadata value before it reaches Langfuse.

Rules (PROJECT.md section 7, T009 amendment):

* **Tool outputs are denied by default.** A tool message (LangChain
  ``ToolMessage`` or its dict form with ``role``/``type`` ``tool``) keeps its
  content only for ``explain_sql`` (an estimate) and for the ``run_sql`` summary
  (columns, row count,
  truncation flag, SQL, result path). Every other tool output, including
  ``read_file``/``grep``/``ls`` over stored results, eviction previews and
  subagent ``task`` results, becomes ``<redacted tool output: NAME>``. The tool
  name comes from the message itself or from the ``tool_calls`` of an assistant
  message in the same list, so generation inputs echoing tool results are
  covered as well. A tool call id claimed by two tool names, or a message name
  disagreeing with the name its call id maps to, is treated as ambiguous and
  redacted (a spoofed ``explain_sql`` name cannot unlock another tool's output).
* **Tool-call arguments are keys-only** (:mod:`rootcause.observability.tool_args`):
  every tool call (LangChain ``tool_calls`` entries, OpenAI ``function``
  objects, Anthropic ``tool_use`` blocks) keeps only allowlisted argument keys,
  because the model copies rows into ``write_file`` content, ``edit_file``
  strings, ``task`` descriptions and todos.
* **Agent state is keys-only**: ``files`` keeps paths with a marker instead of
  the file data, ``todos`` keeps each todo's status.
* **Long strings are dropped**: a string over 32 KiB becomes
  ``<redacted long text: N chars>``.
* **Stored results are dropped.** Mapping entries keyed by a path under
  ``/query_results/`` or ``/large_tool_results/`` (agent filesystem state) are
  removed.
* **SQL result payloads lose their rows** wherever they appear (a mapping with
  both ``columns`` and ``rows``).
* **Every string is scrubbed**: credentials via the log redactor, then, when
  enabled, the PII pass (:mod:`rootcause.observability.pii`).

The mask reaches into JSON text and into LangChain messages, other pydantic
models and dataclasses (``langgraph.types.Command``). It never mutates its input
and always returns JSON-serialisable data.
"""

import dataclasses
import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final

from pydantic import BaseModel

from rootcause.logging import make_text_redactor
from rootcause.observability.pii import redact_pii
from rootcause.observability.tool_args import (
    FILE_CONTENT_MARKER,
    FILES_KEY,
    TODOS_KEY,
    reduce_arguments,
    reduce_todos,
)

ROWS_KEY: Final = "rows"
COLUMNS_KEY: Final = "columns"
ROW_COUNT_KEY: Final = "row_count"
CONTENT_KEY: Final = "content"
# Tool message fields that may carry tool output besides ``content``.
TOOL_PAYLOAD_KEYS: Final = frozenset({CONTENT_KEY, "artifact"})
TOOL_MARKERS: Final = frozenset({"tool", "tool_result"})
TOOL_OUTPUT_MARKER: Final = "<redacted tool output: {name}>"
UNKNOWN_TOOL: Final = "unknown"
AMBIGUOUS_TOOL: Final = "ambiguous"
# Tools whose output carries no customer data (write_todos echoes model-written todos).
KEPT_TOOL_OUTPUTS: Final = frozenset({"explain_sql"})
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
LONG_TEXT_LIMIT: Final = 32 * 1024
LONG_TEXT_MARKER: Final = "<redacted long text: {length} chars>"
NAME_KEY: Final = "name"
ARGS_KEY: Final = "args"
FUNCTION_KEY: Final = "function"
ARGUMENTS_KEY: Final = "arguments"
TOOL_USE_TYPE: Final = "tool_use"
INPUT_KEY: Final = "input"

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


def _merged_names(names: ToolNames, pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """``names`` plus ``pairs``; an id seen with two different names becomes ambiguous."""
    merged = dict(names)
    for call_id, name in pairs:
        known = merged.get(call_id)
        merged[call_id] = name if known in (None, name) else AMBIGUOUS_TOOL
    return merged


def _tool_names(items: list[Any], names: ToolNames) -> dict[str, str]:
    """``names`` extended with the tool call ids of the assistant messages in ``items``."""
    pairs = [
        pair
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get("tool_calls"), list)
        for pair in map(_call_name, item["tool_calls"])
        if pair is not None
    ]
    return _merged_names(names, pairs)


def _resolved_tool_name(message_name: Any, mapped: str | None) -> tuple[str, bool]:
    """The tool name for the marker, and whether it may unlock an allowlisted output.

    A name is trusted only when the message name and the call id's name agree (or
    only one of them exists and the id is not ambiguous).
    """
    has_name = isinstance(message_name, str) and bool(message_name)
    if mapped == AMBIGUOUS_TOOL or (mapped is not None and has_name and message_name != mapped):
        return (str(message_name) if has_name else AMBIGUOUS_TOOL), False
    if has_name:
        return str(message_name), True
    return (mapped, True) if mapped is not None else (UNKNOWN_TOOL, True)


def _reduced_call(value: Mapping[Any, Any]) -> Mapping[Any, Any]:
    """A tool call (any provider shape) with its arguments reduced; other values unchanged."""
    name = value.get(NAME_KEY)
    if isinstance(name, str) and ARGS_KEY in value:
        return {**value, ARGS_KEY: reduce_arguments(name, value[ARGS_KEY])}
    if isinstance(name, str) and value.get("type") == TOOL_USE_TYPE and INPUT_KEY in value:
        return {**value, INPUT_KEY: reduce_arguments(name, value[INPUT_KEY])}
    function = value.get(FUNCTION_KEY)
    if isinstance(function, Mapping) and isinstance(function.get(NAME_KEY), str):
        arguments = reduce_arguments(function[NAME_KEY], function.get(ARGUMENTS_KEY))
        return {**value, FUNCTION_KEY: {**function, ARGUMENTS_KEY: arguments}}
    return value


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
            if len(value) > LONG_TEXT_LIMIT:
                return LONG_TEXT_MARKER.format(length=len(value))
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
        scoped = _tool_names(plain, names)
        return [self.mask(item, depth + 1, scoped) for item in plain]

    def _mapping(self, value: Mapping[Any, Any], depth: int, names: ToolNames) -> dict[str, Any]:
        if _is_tool_message(value):
            return self._tool_message(value, depth, names)
        source = _reduced_call(_without_rows(value) if _is_sql_result(value) else value)
        return {
            str(key): self._entry(key, item, depth, names)
            for key, item in source.items()
            if not _is_result_path(key)
        }

    def _entry(self, key: Any, item: Any, depth: int, names: ToolNames) -> Any:
        """Agent state entries are keys-only; anything else is masked recursively."""
        if key == FILES_KEY and isinstance(item, Mapping):
            return {
                self.scrub(str(path)): FILE_CONTENT_MARKER
                for path in item
                if not _is_result_path(path)
            }
        if key == TODOS_KEY and isinstance(item, list | tuple):
            return self.mask(reduce_todos(item), depth + 1, names)
        return self.mask(item, depth + 1, names)

    def _tool_message(
        self, value: Mapping[Any, Any], depth: int, names: ToolNames
    ) -> dict[str, Any]:
        call_id = value.get("tool_call_id") or value.get("tool_use_id")
        mapped = names.get(call_id) if isinstance(call_id, str) else None
        resolved, trusted = _resolved_tool_name(value.get(NAME_KEY), mapped)
        kept = {
            str(key): self.mask(item, depth + 1, names)
            for key, item in value.items()
            if key not in TOOL_PAYLOAD_KEYS
        }
        content = value.get(CONTENT_KEY)
        masked = self._tool_content(resolved, content, depth) if trusted else _redacted(resolved)
        return {**kept, CONTENT_KEY: masked}

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
