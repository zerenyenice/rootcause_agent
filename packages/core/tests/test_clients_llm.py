"""T008: rootcause_core.clients.chat_model (construction only, no network)."""

from typing import Any, cast

import pytest

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit

SENTINEL = "sk-SENTINEL-123"
ENDPOINT = "https://example.openai.azure.com"
ROLES = ("orchestrator", "fast", "reasoning")


def _anthropic(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/db",
        "llm_provider": "anthropic",
        "anthropic_api_key": SENTINEL,
        "orchestrator_model": "claude-orch",
        "fast_model": "claude-fast",
        "reasoning_model": "claude-reason",
        "llm_timeout_s": 11,
        "llm_max_retries": 4,
        "llm_max_tokens": 777,
    }
    return Settings(_env_file=None, **{**values, **overrides})


def _azure(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/db",
        "llm_provider": "azure_openai",
        "azure_openai_endpoint": ENDPOINT,
        "azure_openai_api_key": SENTINEL,
        "azure_openai_api_version": "2025-01-01",
        "orchestrator_model": "dep-orch",
        "fast_model": "dep-fast",
        "reasoning_model": "dep-reason",
        "llm_timeout_s": 11,
        "llm_max_retries": 4,
        "llm_max_tokens": 777,
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.fixture(autouse=True)
def _fresh_clients() -> Any:
    from rootcause_core.clients import reset_clients

    reset_clients()
    yield
    reset_clients()


@pytest.mark.parametrize(
    ("role", "model"),
    [("orchestrator", "claude-orch"), ("fast", "claude-fast"), ("reasoning", "claude-reason")],
)
def test_anthropic_chat_model_per_role(role: str, model: str) -> None:
    from langchain_anthropic import ChatAnthropic
    from rootcause_core.clients import chat_model

    client = chat_model(cast(Any, role), _anthropic())

    assert isinstance(client, ChatAnthropic)
    assert client.model == model
    assert client.default_request_timeout == 11
    assert client.max_retries == 4
    assert client.max_tokens == 777


@pytest.mark.parametrize("model", ["anthropic:claude-opus-5", "claude-opus-5"])
def test_anthropic_prefix_is_accepted_and_stripped(model: str) -> None:
    from rootcause_core.clients import chat_model

    client = chat_model("orchestrator", _anthropic(orchestrator_model=model))

    assert client.model == "claude-opus-5"


def test_default_settings_models_build_for_anthropic() -> None:
    from rootcause_core.clients import chat_model

    settings = Settings(
        _env_file=None, database_url="postgresql://u:p@localhost/db", anthropic_api_key=SENTINEL
    )

    assert chat_model("fast", settings).model == "claude-sonnet-5"


@pytest.mark.parametrize(
    ("role", "deployment"),
    [("orchestrator", "dep-orch"), ("fast", "dep-fast"), ("reasoning", "dep-reason")],
)
def test_azure_chat_model_per_role(role: str, deployment: str) -> None:
    from langchain_openai import AzureChatOpenAI
    from rootcause_core.clients import chat_model

    client = chat_model(cast(Any, role), _azure())

    assert isinstance(client, AzureChatOpenAI)
    assert client.azure_endpoint == ENDPOINT
    assert client.deployment_name == deployment
    assert client.openai_api_version == "2025-01-01"
    assert client.request_timeout == 11
    assert client.max_retries == 4
    assert client.max_tokens == 777


def test_chat_model_is_cached_per_role() -> None:
    from rootcause_core.clients import chat_model

    settings = _anthropic()

    assert chat_model("fast", settings) is chat_model("fast", settings)
    assert chat_model("fast", settings) is not chat_model("reasoning", settings)


def test_equal_settings_share_a_client_and_different_settings_do_not() -> None:
    from rootcause_core.clients import chat_model

    assert chat_model("fast", _anthropic()) is chat_model("fast", _anthropic())
    assert chat_model("fast", _anthropic()) is not chat_model(
        "fast", _anthropic(llm_max_tokens=778)
    )


def test_reset_clients_drops_the_cache() -> None:
    from rootcause_core.clients import chat_model, reset_clients

    settings = _anthropic()
    first = chat_model("orchestrator", settings)

    reset_clients()

    assert chat_model("orchestrator", settings) is not first


def test_chat_model_defaults_to_get_settings(
    monkeypatch: pytest.MonkeyPatch, base_env: None
) -> None:
    from rootcause_core.clients import chat_model

    monkeypatch.setenv("ROOTCAUSE_ANTHROPIC_API_KEY", SENTINEL)
    monkeypatch.setenv("ROOTCAUSE_FAST_MODEL", "claude-from-env")

    assert chat_model("fast").model == "claude-from-env"


def test_unknown_role_is_rejected() -> None:
    from rootcause_core.clients import chat_model

    with pytest.raises((ConfigurationError, ValueError, KeyError)):
        chat_model(cast(Any, "nonsense"), _anthropic())


def test_anthropic_without_key_raises_configuration_error_naming_variable() -> None:
    from rootcause_core.clients import chat_model

    settings = _anthropic(anthropic_api_key=None)

    with pytest.raises(ConfigurationError) as caught:
        chat_model("orchestrator", settings)

    assert "ROOTCAUSE_ANTHROPIC_API_KEY" in str(caught.value)
    assert "ROOTCAUSE_ANTHROPIC_API_KEY" in caught.value.variables
