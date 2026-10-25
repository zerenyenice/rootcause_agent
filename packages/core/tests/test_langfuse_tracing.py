"""T009: rootcause_core.observability.tracing (callbacks and run config)."""

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _callbacks(**overrides: Any) -> list[Any]:
    from rootcause_core.observability.tracing import trace_callbacks

    kwargs: dict[str, Any] = {
        "session_id": "t1",
        "user_id": None,
        "tags": (),
        "metadata": {},
    }
    return list(trace_callbacks(**{**kwargs, **overrides}))


def test_trace_callbacks_is_empty_when_disabled(base_env: None, langfuse_capture: Any) -> None:
    assert _callbacks() == []
    assert langfuse_capture.init_kwargs == []


def test_trace_callbacks_returns_the_langfuse_handler_when_enabled(
    langfuse_capture: Any, langfuse_env: Any
) -> None:
    from langchain_core.callbacks import BaseCallbackHandler
    from langfuse.langchain import CallbackHandler

    callbacks = _callbacks(user_id="u1", tags=("a",), metadata={"k": "v"})

    assert len(callbacks) == 1
    assert isinstance(callbacks[0], CallbackHandler)
    assert isinstance(callbacks[0], BaseCallbackHandler)


def test_run_config_without_langfuse_only_carries_the_thread_id(
    base_env: None, langfuse_capture: Any
) -> None:
    from rootcause_core.observability.tracing import run_config

    config = run_config("t1")

    assert config["configurable"]["thread_id"] == "t1"
    assert not config.get("callbacks")
    assert langfuse_capture.init_kwargs == []


def test_run_config_carries_thread_callbacks_and_langfuse_metadata(
    langfuse_capture: Any, langfuse_env: Any
) -> None:
    from langfuse.langchain import CallbackHandler
    from rootcause_core.observability.tracing import run_config

    config = run_config("thread-42", user_id="analyst-7", tags=("rootcause", "prod"))

    assert config["configurable"]["thread_id"] == "thread-42"
    [handler] = config["callbacks"]
    assert isinstance(handler, CallbackHandler)
    metadata = config["metadata"]
    assert metadata["langfuse_session_id"] == "thread-42"
    assert metadata["langfuse_user_id"] == "analyst-7"
    # The SDK only honours a real list here; a tuple is silently ignored.
    assert metadata["langfuse_tags"] == ["rootcause", "prod"]
    assert isinstance(metadata["langfuse_tags"], list)


def test_run_config_omits_user_and_tags_when_not_given(
    langfuse_capture: Any, langfuse_env: Any
) -> None:
    from rootcause_core.observability.tracing import run_config

    metadata = run_config("t1")["metadata"]

    assert metadata["langfuse_session_id"] == "t1"
    assert metadata.get("langfuse_user_id") is None
    assert not metadata.get("langfuse_tags")


def test_run_config_returns_a_fresh_config_each_call(
    langfuse_capture: Any, langfuse_env: Any
) -> None:
    from rootcause_core.observability.tracing import run_config

    first = run_config("t1", tags=("a",))
    second = run_config("t2")

    assert first is not second
    assert first["configurable"]["thread_id"] == "t1"
    assert second["configurable"]["thread_id"] == "t2"
    assert first["callbacks"][0] is not second["callbacks"][0]
    assert not second["metadata"].get("langfuse_tags")


@pytest.mark.parametrize(
    "thread_id", ["", "t \N{GRINNING FACE}", "x" * 10_000], ids=["empty", "emoji", "very-long"]
)
def test_run_config_accepts_odd_thread_ids(
    base_env: None, langfuse_capture: Any, thread_id: str
) -> None:
    from rootcause_core.observability.tracing import run_config

    assert run_config(thread_id)["configurable"]["thread_id"] == thread_id
