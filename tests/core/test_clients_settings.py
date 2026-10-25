"""T008: client-related settings defaults and per-provider validation (no network)."""

import pytest

from rootcause.errors import ConfigurationError
from rootcause.settings import Settings, get_settings

pytestmark = pytest.mark.unit

SENTINEL = "sk-SENTINEL-123"
DB = "postgresql://u:p@localhost/db"


def _defaults() -> Settings:
    return Settings(_env_file=None, database_url=DB)


def test_llm_defaults() -> None:
    settings = _defaults()

    assert settings.llm_provider == "anthropic"
    assert settings.llm_timeout_s == 60
    assert settings.llm_max_retries == 2
    assert settings.llm_max_tokens == 8192
    assert settings.anthropic_api_key is None
    assert settings.azure_openai_endpoint is None
    assert settings.azure_openai_api_key is None
    assert settings.azure_openai_api_version == "2025-04-01-preview"


def test_embedding_and_viking_defaults() -> None:
    settings = _defaults()

    assert settings.embedding_provider == "azure_openai"
    assert settings.embedding_dimensions == 1536
    assert settings.embedding_batch_size == 64
    assert settings.embedding_api_base is None
    assert settings.embedding_api_key is None
    assert settings.openviking_api_key is None


def test_secret_fields_are_secret_str() -> None:
    settings = Settings(
        _env_file=None,
        database_url=DB,
        anthropic_api_key=SENTINEL,
        azure_openai_api_key=SENTINEL,
        embedding_api_key=SENTINEL,
        openviking_api_key=SENTINEL,
    )

    for field in (
        settings.anthropic_api_key,
        settings.azure_openai_api_key,
        settings.embedding_api_key,
        settings.openviking_api_key,
    ):
        assert field is not None
        assert field.get_secret_value() == SENTINEL
    assert SENTINEL not in repr(settings)


def test_loads_client_fields_from_env(monkeypatch: pytest.MonkeyPatch, base_env: None) -> None:
    monkeypatch.setenv("ROOTCAUSE_LLM_PROVIDER", "azure_openai")
    monkeypatch.setenv("ROOTCAUSE_AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("ROOTCAUSE_AZURE_OPENAI_API_KEY", SENTINEL)
    monkeypatch.setenv("ROOTCAUSE_LLM_MAX_TOKENS", "123")

    settings = get_settings()

    assert settings.llm_provider == "azure_openai"
    assert settings.llm_max_tokens == 123
    assert settings.azure_openai_api_key is not None


@pytest.mark.parametrize(
    ("present", "missing"),
    [
        ({"AZURE_OPENAI_API_KEY": SENTINEL}, "ROOTCAUSE_AZURE_OPENAI_ENDPOINT"),
        (
            {"AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com"},
            "ROOTCAUSE_AZURE_OPENAI_API_KEY",
        ),
    ],
)
def test_azure_provider_requires_endpoint_and_key(
    monkeypatch: pytest.MonkeyPatch,
    base_env: None,
    present: dict[str, str],
    missing: str,
) -> None:
    monkeypatch.setenv("ROOTCAUSE_LLM_PROVIDER", "azure_openai")
    for name, value in present.items():
        monkeypatch.setenv(f"ROOTCAUSE_{name}", value)

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert missing in str(caught.value)
    assert missing in caught.value.variables
    assert SENTINEL not in str(caught.value)


def test_azure_provider_names_every_missing_variable(
    monkeypatch: pytest.MonkeyPatch, base_env: None
) -> None:
    monkeypatch.setenv("ROOTCAUSE_LLM_PROVIDER", "azure_openai")

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert {"ROOTCAUSE_AZURE_OPENAI_ENDPOINT", "ROOTCAUSE_AZURE_OPENAI_API_KEY"} <= set(
        caught.value.variables
    )


def test_unknown_llm_provider_is_rejected(monkeypatch: pytest.MonkeyPatch, base_env: None) -> None:
    monkeypatch.setenv("ROOTCAUSE_LLM_PROVIDER", "bedrock")

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert "ROOTCAUSE_LLM_PROVIDER" in str(caught.value)


@pytest.mark.parametrize("name", ["LLM_TIMEOUT_S", "LLM_MAX_TOKENS"])
def test_non_positive_llm_limits_are_rejected(
    monkeypatch: pytest.MonkeyPatch, base_env: None, name: str
) -> None:
    monkeypatch.setenv(f"ROOTCAUSE_{name}", "-1")

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert f"ROOTCAUSE_{name}" in str(caught.value)


def test_llm_max_retries_accepts_zero_and_rejects_negative(
    monkeypatch: pytest.MonkeyPatch, base_env: None
) -> None:
    assert Settings(_env_file=None, database_url=DB, llm_max_retries=0).llm_max_retries == 0

    monkeypatch.setenv("ROOTCAUSE_LLM_MAX_RETRIES", "-1")
    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert "ROOTCAUSE_LLM_MAX_RETRIES" in str(caught.value)


@pytest.mark.parametrize("role", ["ORCHESTRATOR", "FAST", "REASONING"])
def test_role_provider_azure_requires_endpoint_and_key(
    monkeypatch: pytest.MonkeyPatch, base_env: None, role: str
) -> None:
    monkeypatch.setenv(f"ROOTCAUSE_{role}_PROVIDER", "azure_openai")

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert {"ROOTCAUSE_AZURE_OPENAI_ENDPOINT", "ROOTCAUSE_AZURE_OPENAI_API_KEY"} <= set(
        caught.value.variables
    )
    assert SENTINEL not in str(caught.value)


def test_role_provider_azure_with_credentials_is_valid() -> None:
    settings = Settings(
        _env_file=None,
        database_url=DB,
        fast_provider="azure_openai",
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_api_key=SENTINEL,
    )

    assert settings.fast_provider == "azure_openai"
    assert settings.llm_provider == "anthropic"


def test_unknown_role_provider_is_rejected(monkeypatch: pytest.MonkeyPatch, base_env: None) -> None:
    monkeypatch.setenv("ROOTCAUSE_FAST_PROVIDER", "bedrock")

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert "ROOTCAUSE_FAST_PROVIDER" in str(caught.value)
