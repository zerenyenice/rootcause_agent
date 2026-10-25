"""Keys-only view of model-written tool-call arguments and agent state (T009 hardening).

The model copies query rows into tool-call arguments (``write_file`` content,
``edit_file`` strings, ``task`` descriptions, todo texts), so arguments are
denied by default: only the keys in :data:`KEPT_ARGUMENT_KEYS` survive, with
scalar values, and ``write_todos`` keeps each todo's ``status`` alone. The same
rules reduce the agent's ``files`` state (paths only) and ``todos`` state.
"""

import json
from collections.abc import Mapping
from typing import Any, Final

KEPT_ARGUMENT_KEYS: Final = frozenset({"file_path", "path", "subagent_type", "offset", "limit"})
TODOS_KEY: Final = "todos"
STATUS_KEY: Final = "status"
TODO_TOOL: Final = "write_todos"
FILES_KEY: Final = "files"
FILE_CONTENT_MARKER: Final = "<redacted file content>"


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def reduce_todos(todos: Any) -> list[dict[str, Any]]:
    """Each todo reduced to its ``status`` (a todo without a scalar status becomes ``{}``)."""
    if not isinstance(todos, list | tuple):
        return []
    return [
        {STATUS_KEY: todo[STATUS_KEY]}
        if isinstance(todo, Mapping) and _is_scalar(todo.get(STATUS_KEY, {}))
        else {}
        for todo in todos
    ]


def _parsed(arguments: Any) -> Any:
    if not isinstance(arguments, str):
        return arguments
    try:
        return json.loads(arguments)
    except ValueError:
        return None


def _reduced_mapping(name: str, arguments: Mapping[Any, Any]) -> dict[str, Any]:
    if name == TODO_TOOL:
        return {TODOS_KEY: reduce_todos(arguments[TODOS_KEY])} if TODOS_KEY in arguments else {}
    return {
        str(key): value
        for key, value in arguments.items()
        if key in KEPT_ARGUMENT_KEYS and _is_scalar(value)
    }


def reduce_arguments(name: str, arguments: Any) -> Any:
    """Return ``arguments`` of tool ``name`` reduced to the allowlisted keys.

    JSON text stays JSON text (OpenAI ``function.arguments``); anything that is
    not a mapping (unparseable or partial JSON, a Python repr) becomes empty.
    """
    parsed = _parsed(arguments)
    reduced = _reduced_mapping(name, parsed) if isinstance(parsed, Mapping) else {}
    return json.dumps(reduced, ensure_ascii=False) if isinstance(arguments, str) else reduced
