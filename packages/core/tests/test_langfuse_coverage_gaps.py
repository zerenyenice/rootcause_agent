"""T009 coverage gaps: tool-call shapes, detach fallback, handler fail-closed, subagents."""

import builtins
import json
from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from opentelemetry import context as otel_context

from rootcause_core.observability.masking import build_mask
from rootcause_core.observability.subagents import (
    MAX_DEPTH,
    SUBAGENT_TASK_MARKER,
    without_human_text,
)
from rootcause_core.observability.tool_args import reduce_arguments

pytestmark = pytest.mark.unit

LEAK = "ROWLEAK-jane.doe@example.com"


@pytest.fixture
def mask() -> Callable[..., Any]:
    return build_mask(mask_pii=False)


def test_openai_function_arguments_keep_only_allowlisted_keys(mask: Callable[..., Any]) -> None:
    call = {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"file_path": "/a.txt", "content": LEAK}),
        },
    }

    out = mask(data={"tool_calls": [call]})

    function = out["tool_calls"][0]["function"]
    assert function["name"] == "write_file"
    assert json.loads(function["arguments"]) == {"file_path": "/a.txt"}
    assert LEAK not in json.dumps(out)


def test_anthropic_tool_use_input_keeps_only_allowlisted_keys(mask: Callable[..., Any]) -> None:
    block = {
        "type": "tool_use",
        "id": "t1",
        "name": "edit_file",
        "input": {"file_path": "/a.txt", "new_string": LEAK, "old_string": LEAK},
    }

    out = mask(data=[{"role": "assistant", "content": [block]}])

    assert out[0]["content"][0]["input"] == {"file_path": "/a.txt"}
    assert LEAK not in json.dumps(out)


def test_langchain_tool_calls_args_are_reduced(mask: Callable[..., Any]) -> None:
    message = AIMessage(
        content="",
        tool_calls=[{"id": "x", "name": "task", "args": {"description": LEAK, "path": "/p"}}],
    )

    out = mask(data=message)

    assert LEAK not in json.dumps(out)
    assert out["tool_calls"][0]["args"] == {"path": "/p"}


def test_json_string_arguments_stay_json_text() -> None:
    result = reduce_arguments("read_file", '{"file_path": "/a", "content": "x", "limit": 5}')

    assert isinstance(result, str)
    assert json.loads(result) == {"file_path": "/a", "limit": 5}


@pytest.mark.parametrize("arguments", ["not json {", '["list"]', "", "42"])
def test_unparseable_or_non_mapping_string_arguments_become_empty_json(arguments: str) -> None:
    assert reduce_arguments("write_file", arguments) == "{}"


def test_non_scalar_allowlisted_argument_values_are_dropped() -> None:
    assert reduce_arguments("read_file", {"path": {"nested": LEAK}, "limit": 3}) == {"limit": 3}


def test_write_todos_arguments_keep_statuses_only() -> None:
    result = reduce_arguments(
        "write_todos", {"todos": [{"content": LEAK, "status": "done"}, "junk"], "x": LEAK}
    )

    assert result == {"todos": [{"status": "done"}, {}]}


def test_non_mapping_non_string_arguments_become_empty_mapping() -> None:
    assert reduce_arguments("write_file", None) == {}
    assert reduce_arguments("write_todos", {"nothing": 1}) == {}


# --- _detach ---


def _block_import(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    real = builtins.__import__

    def fake(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == module or name.startswith(f"{module}."):
            raise ImportError(f"blocked {name}")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)


def test_detach_falls_back_to_opentelemetry_when_the_sdk_helper_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rootcause_core.clients import langfuse as module

    detached: list[object] = []
    monkeypatch.setattr(otel_context, "detach", detached.append)
    _block_import(monkeypatch, "langfuse._client.propagation")

    module._detach("token")  # noqa: SLF001

    assert detached == ["token"]


def test_detach_fallback_swallows_and_logs_a_failing_opentelemetry_detach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rootcause_core.clients import langfuse as module

    def boom(_token: object) -> None:
        raise RuntimeError("other context")

    events: list[str] = []
    monkeypatch.setattr(otel_context, "detach", boom)
    monkeypatch.setattr(module._logger, "warning", lambda event, **_k: events.append(event))  # noqa: SLF001
    _block_import(monkeypatch, "langfuse._client.propagation")

    module._detach("token")  # noqa: SLF001

    assert events == ["langfuse_context_detach_failed"]


# --- callback handler fails closed ---


def test_callback_handler_is_none_and_logs_when_langchain_integration_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Any],
) -> None:
    from rootcause_core.clients import langfuse as module

    events: list[str] = []
    monkeypatch.setattr(module._logger, "error", lambda event, **_k: events.append(event))  # noqa: SLF001
    settings = make_langfuse_settings()
    assert module.langfuse_client(settings) is not None
    _block_import(monkeypatch, "langfuse.langchain")

    handler = module.langfuse_callback_handler(settings)

    assert handler is None
    assert events == ["langfuse_langchain_unavailable"]


def _handler_without_error_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap the SDK handler for one whose private error hook is gone (SDK drift)."""
    import langfuse.langchain as sdk

    hook = "_get_error_level_and_status_message"

    class DriftedHandler(sdk.CallbackHandler):
        def __getattribute__(self, name: str) -> Any:
            if name == hook:
                raise AttributeError(name)
            return super().__getattribute__(name)

    monkeypatch.setattr(sdk, "CallbackHandler", DriftedHandler)


def test_callback_handler_is_none_and_logs_when_the_sdk_error_hook_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Any],
) -> None:
    from rootcause_core.clients import langfuse as module

    events: list[str] = []
    monkeypatch.setattr(module._logger, "error", lambda event, **_k: events.append(event))  # noqa: SLF001
    settings = make_langfuse_settings()
    _handler_without_error_hook(monkeypatch)

    handler = module.langfuse_callback_handler(settings)

    assert handler is None
    assert events == ["langfuse_handler_guard_unavailable"]


def test_run_config_carries_no_callbacks_when_the_sdk_error_hook_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    langfuse_capture: Any,
    make_langfuse_settings: Callable[..., Any],
) -> None:
    from rootcause_core.observability.tracing import run_config

    settings = make_langfuse_settings()
    _handler_without_error_hook(monkeypatch)

    config = run_config("thread-1", tenant_id="t1", settings=settings)

    assert "callbacks" not in config
    assert config["configurable"] == {"thread_id": "thread-1"}
    assert config["metadata"] == {"tenant_id": "t1"}


# --- subagent redaction ---


def test_plain_dict_human_messages_are_redacted_without_mutating_input() -> None:
    state = {
        "messages": [
            {"role": "user", "content": LEAK},
            {"type": "human", "content": LEAK},
            {"role": "assistant", "content": "ok"},
        ]
    }

    out = without_human_text(state)

    assert [m["content"] for m in out["messages"]] == [
        SUBAGENT_TASK_MARKER,
        SUBAGENT_TASK_MARKER,
        "ok",
    ]
    assert state["messages"][0]["content"] == LEAK


def test_human_message_objects_are_redacted_without_mutating_input() -> None:
    message = HumanMessage(content=LEAK)

    out = without_human_text({"messages": (message,)})

    assert out["messages"][0].content == SUBAGENT_TASK_MARKER
    assert message.content == LEAK


def test_values_beyond_the_depth_limit_are_dropped() -> None:
    nested: Any = LEAK
    for _ in range(MAX_DEPTH + 2):
        nested = {"k": nested}

    out = without_human_text(nested)

    assert LEAK not in json.dumps(out)
    assert SUBAGENT_TASK_MARKER in json.dumps(out)
