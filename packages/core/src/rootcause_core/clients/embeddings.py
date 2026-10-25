"""Embeddings factory: the only place provider embedding classes are constructed."""

from functools import cache

from langchain_core.embeddings import Embeddings
from langchain_openai import AzureOpenAIEmbeddings, OpenAIEmbeddings
from pydantic import AnyHttpUrl, SecretStr

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import ENV_PREFIX, Settings, get_settings

# OpenAI-compatible servers that need no key still require a non-empty value.
PLACEHOLDER_KEY = "not-needed"


def _require_url(value: AnyHttpUrl | None, variable: str) -> str:
    if value is None:
        raise ConfigurationError(
            f"{ENV_PREFIX}{variable} is required for the selected provider.",
            variables=(f"{ENV_PREFIX}{variable}",),
        )
    return str(value).rstrip("/")


def _azure(settings: Settings) -> Embeddings:
    endpoint = _require_url(settings.azure_openai_endpoint, "AZURE_OPENAI_ENDPOINT")
    if settings.azure_openai_api_key is None:
        raise ConfigurationError(
            f"{ENV_PREFIX}AZURE_OPENAI_API_KEY is required for the selected provider.",
            variables=(f"{ENV_PREFIX}AZURE_OPENAI_API_KEY",),
        )
    return AzureOpenAIEmbeddings(  # type: ignore[call-arg]
        azure_deployment=settings.embedding_model,
        azure_endpoint=endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        dimensions=settings.embedding_dimensions,
        chunk_size=settings.embedding_batch_size,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )


def _compatible(settings: Settings) -> Embeddings:
    base = _require_url(settings.embedding_api_base, "EMBEDDING_API_BASE")
    key = settings.embedding_api_key or SecretStr(PLACEHOLDER_KEY)
    return OpenAIEmbeddings(  # type: ignore[call-arg]
        model=settings.embedding_model,
        base_url=base,
        api_key=key,
        dimensions=settings.embedding_dimensions,
        chunk_size=settings.embedding_batch_size,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
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
