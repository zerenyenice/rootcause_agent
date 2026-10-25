"""Service client factories; the only package that builds provider SDK clients."""

from rootcause_core.clients.embeddings import embeddings, reset_embeddings_cache
from rootcause_core.clients.llm import Role, chat_model, reset_llm_cache
from rootcause_core.clients.openviking import close_clients, openviking_http, reset_http_cache


def reset_clients() -> None:
    """Drop every cached client (tests); HTTP clients are not closed, see close_clients."""
    reset_llm_cache()
    reset_embeddings_cache()
    reset_http_cache()


__all__ = [
    "Role",
    "chat_model",
    "close_clients",
    "embeddings",
    "openviking_http",
    "reset_clients",
]
