"""T008: secrets never surface in client reprs or in error messages."""

import asyncio
from typing import Any, cast

import pytest

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit

SENTINEL = "sk-SENTINEL-123"
DB_SECRET = "s3cr3t-pw"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": f"postgresql://u:{DB_SECRET}@localhost/db",
        "anthropic_api_key": SENTINEL,
        "azure_openai_endpoint": "https://example.openai.azure.com",
        "azure_openai_api_key": SENTINEL,
        "embedding_api_base": "http://embeddings.local/v1",
        "embedding_api_key": SENTINEL,
        "openviking_url": "http://viking.local:1933",
        "openviking_api_key": SENTINEL,
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.fixture(autouse=True)
def _fresh_clients() -> Any:
    from rootcause_core.clients import close_clients, reset_clients

    reset_clients()
    yield
    asyncio.run(close_clients())
    reset_clients()


@pytest.mark.parametrize("provider", ["anthropic", "azure_openai"])
@pytest.mark.parametrize("role", ["orchestrator", "fast", "reasoning"])
def test_chat_model_repr_hides_key(provider: str, role: str) -> None:
    from rootcause_core.clients import chat_model

    client = chat_model(cast(Any, role), _settings(llm_provider=provider))

    assert SENTINEL not in repr(client)
    assert SENTINEL not in str(client)


@pytest.mark.parametrize("provider", ["azure_openai", "openai_compatible"])
def test_embeddings_repr_hides_key(provider: str) -> None:
    from rootcause_core.clients import embeddings

    client = embeddings(_settings(embedding_provider=provider))

    assert SENTINEL not in repr(client)
    assert SENTINEL not in str(client)


def test_openviking_client_repr_hides_key() -> None:
    from rootcause_core.clients import openviking_http

    async def run() -> None:
        assert SENTINEL not in repr(openviking_http(_settings()))

    asyncio.run(run())


def test_settings_repr_hides_every_secret() -> None:
    text = repr(_settings())

    assert SENTINEL not in text
    assert DB_SECRET not in text


def test_construction_errors_hide_secrets() -> None:
    from rootcause_core.clients import chat_model, embeddings, openviking_http

    async def viking() -> None:
        openviking_http(_settings(openviking_url=None))

    failures = (
        lambda: chat_model("fast", _settings(anthropic_api_key=None)),
        lambda: embeddings(_settings(azure_openai_endpoint=None)),
        lambda: embeddings(
            _settings(embedding_provider="openai_compatible", embedding_api_base=None)
        ),
        lambda: asyncio.run(viking()),
    )
    for failure in failures:
        with pytest.raises(ConfigurationError) as caught:
            failure()
        message = f"{caught.value} {caught.value.public_message} {caught.value.variables}"
        assert SENTINEL not in message
        assert DB_SECRET not in message
        assert "ROOTCAUSE_" in message


def test_settings_validation_error_hides_secrets(
    monkeypatch: pytest.MonkeyPatch, base_env: None
) -> None:
    from rootcause_core.settings import get_settings

    monkeypatch.setenv("ROOTCAUSE_LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("ROOTCAUSE_AZURE_OPENAI_API_KEY", SENTINEL)
    monkeypatch.setenv("ROOTCAUSE_ANTHROPIC_API_KEY", SENTINEL)

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert SENTINEL not in str(caught.value)
    assert DB_SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
