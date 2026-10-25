"""Docs test: deploy/.env.example documents the client configuration variables."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / "deploy" / ".env.example"

REQUIRED = (
    "ROOTCAUSE_LLM_PROVIDER",
    "ROOTCAUSE_ANTHROPIC_API_KEY",
    "ROOTCAUSE_AZURE_OPENAI_ENDPOINT",
    "ROOTCAUSE_AZURE_OPENAI_API_KEY",
    "ROOTCAUSE_EMBEDDING_PROVIDER",
    "ROOTCAUSE_EMBEDDING_MODEL",
    "ROOTCAUSE_OPENVIKING_API_KEY",
    "ROOTCAUSE_LANGFUSE_ENABLED",
    "ROOTCAUSE_LANGFUSE_HOST",
    "ROOTCAUSE_LANGFUSE_PUBLIC_KEY",
    "ROOTCAUSE_LANGFUSE_SECRET_KEY",
    "ROOTCAUSE_LANGFUSE_RELEASE",
    "ROOTCAUSE_LANGFUSE_SAMPLE_RATE",
    "ROOTCAUSE_LANGFUSE_MASK_PII",
    "ROOTCAUSE_PROMPT_SOURCE",
    "ROOTCAUSE_PROMPT_LABEL",
    "ROOTCAUSE_PROMPT_CACHE_TTL_S",
)


def test_env_example_exists() -> None:
    assert ENV_EXAMPLE.is_file()


@pytest.mark.parametrize("variable", REQUIRED)
def test_env_example_documents_variable(variable: str) -> None:
    assert variable in ENV_EXAMPLE.read_text(encoding="utf-8")


def test_env_example_contains_no_real_looking_secrets() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "sk-ant-" not in text
    assert "sk-proj-" not in text
