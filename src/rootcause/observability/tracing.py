"""LangChain/LangGraph run configuration that traces a run in Langfuse.

Use :func:`run_config` for every agent invocation: it carries the LangGraph
``thread_id`` and, when ``ROOTCAUSE_LANGFUSE_ENABLED`` is true, a fresh Langfuse
callback handler plus the metadata keys the Langfuse SDK reads at the root run
(``langfuse_session_id`` = thread id, ``langfuse_user_id``, ``langfuse_tags``)
and the opaque ``tenant_id`` / ``connection_id``. Callbacks and metadata
propagate through LangGraph config to subagents, model and tool calls, so a run
produces one trace; the handler starts that trace from an empty OpenTelemetry
context, so it is never nested under the caller's span.
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

from rootcause.clients.langfuse import langfuse_callback_handler
from rootcause.settings import Settings, get_settings

SESSION_ID_KEY = "langfuse_session_id"
USER_ID_KEY = "langfuse_user_id"
TAGS_KEY = "langfuse_tags"
TENANT_ID_KEY = "tenant_id"
CONNECTION_ID_KEY = "connection_id"


def trace_callbacks(settings: Settings | None = None) -> list[BaseCallbackHandler]:
    """Return the callback handlers that trace a run (empty when Langfuse is disabled).

    Session, user, tags and tenant metadata are read from the run metadata, not
    from the handler: use :func:`run_config`, which sets both.
    """
    handler = langfuse_callback_handler(settings)
    return [] if handler is None else [handler]


def _run_metadata(
    *,
    tenant_id: str | None,
    connection_id: str | None,
) -> dict[str, Any]:
    optional = {TENANT_ID_KEY: tenant_id, CONNECTION_ID_KEY: connection_id}
    return {key: value for key, value in optional.items() if value is not None}


def _trace_attributes(
    *, session_id: str, user_id: str | None, tags: Sequence[str]
) -> dict[str, Any]:
    """Langfuse trace attributes; tags become a real list (the SDK ignores tuples)."""
    attributes: dict[str, Any] = {SESSION_ID_KEY: session_id}
    if user_id is not None:
        attributes[USER_ID_KEY] = user_id
    if tags:
        attributes[TAGS_KEY] = list(tags)
    return attributes


def run_config(
    thread_id: str,
    *,
    user_id: str | None = None,
    tags: Sequence[str] = (),
    tenant_id: str | None = None,
    connection_id: str | None = None,
    settings: Settings | None = None,
) -> RunnableConfig:
    """Build a fresh run config for ``thread_id``; traced when Langfuse is enabled.

    ``tenant_id`` and ``connection_id`` are opaque identifiers (never names or
    DSNs); they land in the run metadata and on the Langfuse trace.
    """
    resolved = settings if settings is not None else get_settings()
    configurable = {"thread_id": thread_id}
    metadata = _run_metadata(tenant_id=tenant_id, connection_id=connection_id)
    callbacks = trace_callbacks(resolved)
    if not callbacks:
        if not metadata:
            return RunnableConfig(configurable=configurable)
        return RunnableConfig(configurable=configurable, metadata=metadata)
    trace_attributes = _trace_attributes(session_id=thread_id, user_id=user_id, tags=tags)
    return RunnableConfig(
        configurable=configurable,
        callbacks=callbacks,
        metadata={**metadata, **trace_attributes},
    )
