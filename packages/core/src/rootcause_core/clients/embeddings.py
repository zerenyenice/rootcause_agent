"""Embeddings factory: the only place provider embedding classes are constructed.

``check_embedding_ctx_length`` is disabled for both providers: it would make
langchain-openai download a tiktoken encoding at first use (network access
outside the configured endpoint) and it cannot tokenise for non-OpenAI models
behind an OpenAI-compatible server anyway. Inputs are sent as-is.

Construction is lazy: nothing validates the embedding configuration until the
first :func:`embeddings` call (the first consumer is catalog search, T025).
"""

from functools import cache

from langchain_core.embeddings import Embeddings
from langchain_openai import AzureOpenAIEmbeddings, OpenAIEmbeddings
from pydantic import SecretStr

from rootcause_core.clients._common import _require
from rootcause_core.settings import Settings, get_settings

# OpenAI-compatible servers that need no key still require a non-empty value.
PLACEHOLDER_KEY = "not-needed"


def _azure(settings: Settings) -> Embeddings:
    endpoint = _require(settings.azure_openai_endpoint, "AZURE_OPENAI_ENDPOINT")
    api_key = _require(settings.azure_openai_api_key, "AZURE_OPENAI_API_KEY")
    return AzureOpenAIEmbeddings(
        deployment=settings.embedding_model,
        azure_endpoint=str(endpoint).rstrip("/"),
        openai_api_key=api_key,
        openai_api_version=settings.azure_openai_api_version,
        dimensions=settings.embedding_dimensions,
        chunk_size=settings.embedding_batch_size,
        request_timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        check_embedding_ctx_length=False,
    )


def _compatible(settings: Settings) -> Embeddings:
    base = _require(settings.embedding_api_base, "EMBEDDING_API_BASE")
    api_key = settings.embedding_api_key or SecretStr(PLACEHOLDER_KEY)
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_base=str(base).rstrip("/"),
        openai_api_key=api_key,
        dimensions=settings.embedding_dimensions,
        chunk_size=settings.embedding_batch_size,
        request_timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        check_embedding_ctx_length=False,
    )


@cache
def _build(settings: Settings) -> Embeddings:
    if settings.embedding_provider == "azure_openai":
        return _azure(settings)
    return _compatible(settings)


def embeddings(settings: Settings | None = None) -> Embeddings:
    """Return the cached embeddings client for the configured provider.

    Raises:
        ConfigurationError: a required endpoint, base URL or key is unset.
    """
    return _build(settings if settings is not None else get_settings())


def reset_embeddings_cache() -> None:
    """Drop cached embeddings clients."""
    _build.cache_clear()
