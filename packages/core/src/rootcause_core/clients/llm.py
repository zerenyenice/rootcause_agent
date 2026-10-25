"""Chat-model factory: the only place provider chat SDK classes are constructed."""

from functools import cache
from typing import Any, Literal, get_args

from langchain_anthropic import ChatAnthropic
from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import ENV_PREFIX, Settings, get_settings

Role = Literal["orchestrator", "fast", "reasoning"]
ANTHROPIC_PREFIX = "anthropic:"


def _require_secret(value: SecretStr | None, variable: str) -> str:
    if value is None:
        raise ConfigurationError(
            f"{ENV_PREFIX}{variable} is required for the selected provider.",
            variables=(f"{ENV_PREFIX}{variable}",),
        )
    return value.get_secret_value()


def _model_name(role: Role, settings: Settings) -> str:
    if role == "orchestrator":
        return settings.orchestrator_model
    if role == "fast":
        return settings.fast_model
    return settings.reasoning_model


def _anthropic(name: str, settings: Settings) -> ChatAnthropic:
    key = _require_secret(settings.anthropic_api_key, "ANTHROPIC_API_KEY")
    return ChatAnthropic(
        model=name.removeprefix(ANTHROPIC_PREFIX),
        api_key=SecretStr(key),
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
    )


def _azure(name: str, settings: Settings) -> AzureChatOpenAI:
    key = _require_secret(settings.azure_openai_api_key, "AZURE_OPENAI_API_KEY")
    if settings.azure_openai_endpoint is None:
        raise ConfigurationError(
            f"{ENV_PREFIX}AZURE_OPENAI_ENDPOINT is required for the selected provider.",
            variables=(f"{ENV_PREFIX}AZURE_OPENAI_ENDPOINT",),
        )
    return AzureChatOpenAI(
        azure_deployment=name,
        azure_endpoint=str(settings.azure_openai_endpoint).rstrip("/"),
        api_key=SecretStr(key),
        api_version=settings.azure_openai_api_version,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
    )


@cache
def _build(role: Role, settings: Settings) -> Any:
    name = _model_name(role, settings)
    if settings.llm_provider == "azure_openai":
        return _azure(name, settings)
    return _anthropic(name, settings)


def chat_model(role: Role, settings: Settings | None = None) -> Any:
    """Return the cached chat model for ``role`` per the configured provider.

    Typed ``Any`` (a ``BaseChatModel`` subclass at runtime) so callers can read
    provider attributes such as ``model``.

    Raises:
        ConfigurationError: unknown role, or a required key/endpoint is unset.
    """
    if role not in get_args(Role):
        raise ConfigurationError(f"Unknown chat model role: {role!r}.")
    return _build(role, settings if settings is not None else get_settings())


def reset_llm_cache() -> None:
    """Drop cached chat models."""
    _build.cache_clear()
