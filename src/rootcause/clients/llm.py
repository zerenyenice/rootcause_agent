"""Chat-model factory: the only place provider chat SDK classes are constructed.

Each role (orchestrator, fast, reasoning) resolves its provider through
:meth:`Settings.provider_for`: the role's ``ROOTCAUSE_<ROLE>_PROVIDER`` override
when set, otherwise ``ROOTCAUSE_LLM_PROVIDER``. Azure OpenAI models also get the
role's ``ROOTCAUSE_<ROLE>_REASONING_EFFORT`` when set; Anthropic models ignore it.
"""

from functools import cache
from typing import get_args

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI

from rootcause.clients._common import _require
from rootcause.errors import ConfigurationError
from rootcause.settings import LlmRole, ReasoningEffort, Settings, get_settings

Role = LlmRole
ANTHROPIC_PREFIX = "anthropic:"


def _model_name(role: Role, settings: Settings) -> str:
    if role == "orchestrator":
        return settings.orchestrator_model
    if role == "fast":
        return settings.fast_model
    return settings.reasoning_model


def _anthropic(name: str, settings: Settings) -> ChatAnthropic:
    return ChatAnthropic(
        model=name.removeprefix(ANTHROPIC_PREFIX),
        api_key=_require(settings.anthropic_api_key, "ANTHROPIC_API_KEY"),
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
    )


def _azure(name: str, settings: Settings, effort: ReasoningEffort | None) -> AzureChatOpenAI:
    api_key = _require(settings.azure_openai_api_key, "AZURE_OPENAI_API_KEY")
    endpoint = _require(settings.azure_openai_endpoint, "AZURE_OPENAI_ENDPOINT")
    return AzureChatOpenAI(
        azure_deployment=name,
        azure_endpoint=str(endpoint).rstrip("/"),
        api_key=api_key,
        api_version=settings.azure_openai_api_version,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
        reasoning_effort=effort,
    )


@cache
def _build(role: Role, settings: Settings) -> BaseChatModel:
    name = _model_name(role, settings)
    if settings.provider_for(role) == "azure_openai":
        return _azure(name, settings, settings.reasoning_effort_for(role))
    return _anthropic(name, settings)


def chat_model(role: Role, settings: Settings | None = None) -> BaseChatModel:
    """Return the cached chat model for ``role`` per that role's resolved provider.

    Raises:
        ConfigurationError: unknown role, or a required key/endpoint is unset.
    """
    if role not in get_args(Role):
        raise ConfigurationError(f"Unknown chat model role: {role!r}.")
    return _build(role, settings if settings is not None else get_settings())


def reset_llm_cache() -> None:
    """Drop cached chat models."""
    _build.cache_clear()
