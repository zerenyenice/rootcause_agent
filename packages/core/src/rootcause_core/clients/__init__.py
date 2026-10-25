"""Service client factories; the only package that builds provider SDK clients."""

import asyncio

from rootcause_core.clients.embeddings import embeddings, reset_embeddings_cache
from rootcause_core.clients.langfuse import (
    flush_langfuse,
    langfuse_client,
    reset_langfuse_cache,
    shutdown_langfuse,
)
from rootcause_core.clients.llm import Role, chat_model, reset_llm_cache
from rootcause_core.clients.openviking import close_clients as close_http_clients
from rootcause_core.clients.openviking import openviking_http, reset_http_cache


async def close_clients() -> None:
    """Release every client at process shutdown (API lifespan, T035).

    Closes the cached HTTP clients, then flushes and shuts down every Langfuse
    client in a worker thread (the SDK flush is blocking network I/O). Langfuse
    shutdown runs even when closing an HTTP client fails, so buffered traces are
    not lost; each Langfuse client is shut down independently.
    """
    try:
        await close_http_clients()
    finally:
        await asyncio.to_thread(shutdown_langfuse)


def reset_clients() -> None:
    """Drop every cached client (tests); nothing is closed, see close_clients."""
    reset_llm_cache()
    reset_embeddings_cache()
    reset_http_cache()
    reset_langfuse_cache()


__all__ = [
    "Role",
    "chat_model",
    "close_clients",
    "embeddings",
    "flush_langfuse",
    "langfuse_client",
    "openviking_http",
    "reset_clients",
]
