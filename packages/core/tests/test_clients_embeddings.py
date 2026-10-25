"""T008: rootcause_core.clients.embeddings (construction only, no network)."""

from typing import Any

import pytest

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit

SENTINEL = "sk-SENTINEL-123"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/db",
        "embedding_model": "emb-model",
        "embedding_dimensions": 256,
        "embedding_batch_size": 17,
    }
    return Settings(_env_file=None, **{**values, **overrides})


def _azure(**overrides: Any) -> Settings:
    return _settings(
        **{
            "embedding_provider": "azure_openai",
            "azure_openai_endpoint": "https://example.openai.azure.com",
            "azure_openai_api_key": SENTINEL,
            **overrides,
        }
    )


def _compatible(**overrides: Any) -> Settings:
    return _settings(
        **{
            "embedding_provider": "openai_compatible",
            "embedding_api_base": "http://embeddings.local:8080/v1",
            "embedding_api_key": SENTINEL,
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def _fresh_clients() -> Any:
    from rootcause_core.clients import reset_clients

    reset_clients()
    yield
    reset_clients()


def test_azure_embeddings() -> None:
    from langchain_openai import AzureOpenAIEmbeddings

    from rootcause_core.clients import embeddings

    client = embeddings(_azure())

    assert isinstance(client, AzureOpenAIEmbeddings)
    assert client.deployment == "emb-model"
    assert client.dimensions == 256
    assert client.chunk_size == 17
    assert client.check_embedding_ctx_length is False


def test_openai_compatible_embeddings() -> None:
    from langchain_openai import OpenAIEmbeddings

    from rootcause_core.clients import embeddings

    client = embeddings(_compatible())

    assert isinstance(client, OpenAIEmbeddings)
    assert client.model == "emb-model"
    assert str(client.openai_api_base).startswith("http://embeddings.local:8080/v1")
    assert client.dimensions == 256
    assert client.chunk_size == 17
    assert client.check_embedding_ctx_length is False


def test_azure_embeddings_are_not_the_openai_class() -> None:
    from langchain_openai import AzureOpenAIEmbeddings

    from rootcause_core.clients import embeddings

    assert isinstance(embeddings(_azure()), AzureOpenAIEmbeddings)
    assert not isinstance(embeddings(_compatible()), AzureOpenAIEmbeddings)


def test_embeddings_are_cached_until_reset() -> None:
    from rootcause_core.clients import embeddings, reset_clients

    settings = _azure()
    first = embeddings(settings)

    assert embeddings(settings) is first
    reset_clients()
    assert embeddings(settings) is not first


def test_azure_embeddings_missing_endpoint_names_variable() -> None:
    from rootcause_core.clients import embeddings

    with pytest.raises(ConfigurationError) as caught:
        embeddings(_azure(azure_openai_endpoint=None))

    assert "ROOTCAUSE_AZURE_OPENAI_ENDPOINT" in str(caught.value)
    assert SENTINEL not in str(caught.value)


def test_openai_compatible_missing_base_names_variable() -> None:
    from rootcause_core.clients import embeddings

    with pytest.raises(ConfigurationError) as caught:
        embeddings(_compatible(embedding_api_base=None))

    assert "ROOTCAUSE_EMBEDDING_API_BASE" in str(caught.value)
    assert SENTINEL not in str(caught.value)
