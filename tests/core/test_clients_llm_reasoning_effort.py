"""Per-role reasoning effort for chat models (RED).

Seams: ``Settings.orchestrator_reasoning_effort``, ``fast_reasoning_effort`` and
``reasoning_reasoning_effort`` (``Literal["none", "minimal", "low", "medium",
"high"] | None``, default ``None`` = provider default), read from
``ROOTCAUSE_<ROLE>_REASONING_EFFORT``. Azure OpenAI chat models get the role's
``reasoning_effort``; Anthropic ignores it without error.
"""

from typing import Any, cast

import pytest
from pydantic import ValidationError

from rootcause.settings import Settings

pytestmark = pytest.mark.unit

FAKE_KEY = "sk-SENTINEL-effort"  # gitleaks:allow
ENDPOINT = "https://example.openai.azure.com"
DB_URL = "postgresql://u:p@localhost/db"
ROLES = ("orchestrator", "fast", "reasoning")
EFFORTS = ("none", "minimal", "low", "medium", "high")


def _azure(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": DB_URL,
        "llm_provider": "azure_openai",
        "azure_openai_endpoint": ENDPOINT,
        "azure_openai_api_key": FAKE_KEY,
    }
    return Settings(_env_file=None, **{**values, **overrides})


def _anthropic(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": DB_URL,
        "llm_provider": "anthropic",
        "anthropic_api_key": FAKE_KEY,
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.fixture(autouse=True)
def _fresh_clients() -> Any:
    from rootcause.clients import reset_clients

    reset_clients()
    yield
    reset_clients()


@pytest.mark.parametrize("role", ROLES)
def test_reasoning_effort_defaults_to_provider_default(role: str) -> None:
    assert _anthropic().model_dump()[f"{role}_reasoning_effort"] is None


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("effort", EFFORTS)
def test_reasoning_effort_reads_each_role_from_its_environment_variable(
    monkeypatch: pytest.MonkeyPatch, role: str, effort: str
) -> None:
    monkeypatch.setenv(f"ROOTCAUSE_{role.upper()}_REASONING_EFFORT", effort)

    dumped = _anthropic().model_dump()

    assert dumped[f"{role}_reasoning_effort"] == effort
    others = [other for other in ROLES if other != role]
    assert [dumped[f"{other}_reasoning_effort"] for other in others] == [None, None]


@pytest.mark.parametrize("value", ["extreme", "", "HIGHEST", 3])
def test_reasoning_effort_rejects_unknown_values(value: object) -> None:
    with pytest.raises(ValidationError):
        _anthropic(orchestrator_reasoning_effort=value)


@pytest.mark.parametrize("role", ROLES)
def test_azure_chat_model_gets_the_role_reasoning_effort(role: str) -> None:
    from langchain_openai import AzureChatOpenAI

    from rootcause.clients import chat_model

    client = chat_model(cast(Any, role), _azure(**{f"{role}_reasoning_effort": "medium"}))

    assert isinstance(client, AzureChatOpenAI)
    assert client.reasoning_effort == "medium"


def test_azure_reasoning_effort_is_per_role() -> None:
    from rootcause.clients import chat_model

    settings = _azure(orchestrator_reasoning_effort="high", fast_reasoning_effort="minimal")

    assert getattr(chat_model("orchestrator", settings), "reasoning_effort", None) == "high"
    assert getattr(chat_model("fast", settings), "reasoning_effort", None) == "minimal"
    assert getattr(chat_model("reasoning", settings), "reasoning_effort", None) is None


def test_azure_without_reasoning_effort_leaves_the_provider_default() -> None:
    from rootcause.clients import chat_model

    client = chat_model("orchestrator", _azure())

    assert getattr(client, "reasoning_effort", None) is None
    assert "reasoning_effort" not in getattr(client, "model_kwargs", {})


def test_different_reasoning_effort_builds_a_different_client() -> None:
    from rootcause.clients import chat_model

    low = chat_model("orchestrator", _azure(orchestrator_reasoning_effort="low"))
    high = chat_model("orchestrator", _azure(orchestrator_reasoning_effort="high"))

    assert low is not high
    assert getattr(high, "reasoning_effort", None) == "high"


@pytest.mark.parametrize("role", ROLES)
def test_anthropic_ignores_reasoning_effort_without_error(role: str) -> None:
    from langchain_anthropic import ChatAnthropic

    from rootcause.clients import chat_model

    settings = _anthropic(**{f"{role}_reasoning_effort": "high"})
    client = chat_model(cast(Any, role), settings)

    assert settings.model_dump()[f"{role}_reasoning_effort"] == "high"
    assert isinstance(client, ChatAnthropic)
    assert client.reasoning_effort is None
    assert client.thinking is None
    assert "reasoning_effort" not in client.model_kwargs
