"""LangChain/LangGraph run configuration that traces a run in Langfuse.

Use :func:`run_config` for every agent invocation: it carries the LangGraph
``thread_id`` and, when ``ROOTCAUSE_LANGFUSE_ENABLED`` is true, a fresh Langfuse
callback handler plus the metadata keys the Langfuse SDK reads at the root run
(``langfuse_session_id`` = thread id, ``langfuse_user_id``, ``langfuse_tags``).
Callbacks and metadata propagate through LangGraph config to subagents, model
and tool calls, so a run produces one trace.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

from rootcause_core.clients.langfuse import langfuse_callback_handler
from rootcause_core.settings import Settings, get_settings

SESSION_ID_KEY = "langfuse_session_id"
USER_ID_KEY = "langfuse_user_id"
TAGS_KEY = "langfuse_tags"


def trace_callbacks(
    *,
    session_id: str,
    user_id: str | None,
    tags: Sequence[str],
    metadata: Mapping[str, str],
    settings: Settings | None = None,
) -> list[BaseCallbackHandler]:
    """Return the callback handlers that trace a run (empty when Langfuse is disabled).

    The Langfuse handler takes session, user and tags from the run metadata, not
    from its constructor: pass the same values to :func:`trace_metadata` and put
    the result in the run config's ``metadata`` (:func:`run_config` does both).
    """
    del session_id, user_id, tags, metadata  # read from run metadata, see docstring
    handler = langfuse_callback_handler(settings)
    return [] if handler is None else [handler]


def trace_metadata(
    *,
    session_id: str,
    user_id: str | None,
    tags: Sequence[str],
    metadata: Mapping[str, str],
) -> dict[str, Any]:
    """Build run metadata carrying the Langfuse trace attributes.

    Tags become a real list: the SDK ignores any other sequence type.
    """
    trace_attributes: dict[str, Any] = {SESSION_ID_KEY: session_id}
    if user_id is not None:
        trace_attributes[USER_ID_KEY] = user_id
    if tags:
        trace_attributes[TAGS_KEY] = list(tags)
    return {**metadata, **trace_attributes}


def run_config(
    thread_id: str,
    *,
    user_id: str | None = None,
    tags: Sequence[str] = (),
    settings: Settings | None = None,
) -> RunnableConfig:
    """Build a fresh run config for ``thread_id``; traced when Langfuse is enabled."""
    resolved = settings if settings is not None else get_settings()
    configurable = {"thread_id": thread_id}
    callbacks = trace_callbacks(
        session_id=thread_id, user_id=user_id, tags=tags, metadata={}, settings=resolved
    )
    if not callbacks:
        return RunnableConfig(configurable=configurable)
    return RunnableConfig(
        configurable=configurable,
        callbacks=callbacks,
        metadata=trace_metadata(session_id=thread_id, user_id=user_id, tags=tags, metadata={}),
    )
