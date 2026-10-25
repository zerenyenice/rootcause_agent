"""T009: Langfuse and prompt-source settings (defaults, validation, value-free errors)."""

from typing import Any

import pytest

from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Settings, get_settings

pytestmark = pytest.mark.unit

SENTINEL = "sk-lf-SENTINEL-123"
DB = "postgresql://u:p@localhost/db"
HOST_VARIABLE = "ROOTCAUSE_LANGFUSE_HOST"
KEY_VARIABLES = ("ROOTCAUSE_LANGFUSE_PUBLIC_KEY", "ROOTCAUSE_LANGFUSE_SECRET_KEY")
CREDENTIAL_VARIABLES = (HOST_VARIABLE, *KEY_VARIABLES)


def _full_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_HOST", "http://langfuse.local:3000")
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_PUBLIC_KEY", "pk-lf-1")
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_SECRET_KEY", SENTINEL)


def test_defaults_keep_langfuse_off_and_prompts_in_files() -> None:
    settings = Settings(_env_file=None, database_url=DB)

    assert settings.langfuse_enabled is False
    assert settings.langfuse_host is None
    assert settings.langfuse_public_key is None
    assert settings.langfuse_secret_key is None
    assert settings.langfuse_release is None
    assert settings.langfuse_sample_rate == 1.0
    assert settings.prompt_source == "files"
    assert settings.prompt_label == "production"
    assert settings.prompt_cache_ttl_s == 300


def test_disabled_langfuse_needs_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", DB)

    assert get_settings().langfuse_enabled is False


def test_loads_every_field_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", DB)
    _full_env(monkeypatch)
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_RELEASE", "2026.10.1")
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("ROOTCAUSE_PROMPT_SOURCE", "langfuse")
    monkeypatch.setenv("ROOTCAUSE_PROMPT_LABEL", "staging")
    monkeypatch.setenv("ROOTCAUSE_PROMPT_CACHE_TTL_S", "30")

    settings = get_settings()

    assert settings.langfuse_enabled is True
    assert str(settings.langfuse_host).startswith("http://langfuse.local:3000")
    assert settings.langfuse_public_key is not None
    assert settings.langfuse_public_key.get_secret_value() == "pk-lf-1"
    assert settings.langfuse_secret_key is not None
    assert settings.langfuse_secret_key.get_secret_value() == SENTINEL
    assert settings.langfuse_release == "2026.10.1"
    assert settings.langfuse_sample_rate == 0.25
    assert settings.prompt_source == "langfuse"
    assert settings.prompt_label == "staging"
    assert settings.prompt_cache_ttl_s == 30


def test_keys_are_secret_and_never_shown_in_repr() -> None:
    settings = Settings(
        _env_file=None,
        database_url=DB,
        langfuse_public_key=SENTINEL,
        langfuse_secret_key=SENTINEL,
    )

    assert SENTINEL not in repr(settings)
    assert SENTINEL not in str(settings)


@pytest.mark.parametrize(
    "switch",
    [
        {"ROOTCAUSE_LANGFUSE_ENABLED": "true"},
        {"ROOTCAUSE_PROMPT_SOURCE": "langfuse"},
    ],
)
def test_enabling_langfuse_or_prompt_source_requires_host_and_keys(
    monkeypatch: pytest.MonkeyPatch, base_env: None, switch: dict[str, str]
) -> None:
    for name, value in switch.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert set(CREDENTIAL_VARIABLES) <= set(caught.value.variables)
    for name in CREDENTIAL_VARIABLES:
        assert name in str(caught.value)


@pytest.mark.parametrize("present", CREDENTIAL_VARIABLES)
def test_names_only_the_credentials_that_are_missing(
    monkeypatch: pytest.MonkeyPatch, base_env: None, present: str
) -> None:
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_ENABLED", "true")
    values = {
        HOST_VARIABLE: "http://langfuse.local:3000",
        KEY_VARIABLES[0]: "pk-lf-1",
        KEY_VARIABLES[1]: SENTINEL,
    }
    monkeypatch.setenv(present, values[present])

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert present not in caught.value.variables
    assert set(CREDENTIAL_VARIABLES) - {present} <= set(caught.value.variables)
    assert SENTINEL not in str(caught.value)


def test_credentials_without_the_switch_do_not_enable_anything(
    monkeypatch: pytest.MonkeyPatch, base_env: None
) -> None:
    _full_env(monkeypatch)

    settings = get_settings()

    assert settings.langfuse_enabled is False
    assert settings.prompt_source == "files"


@pytest.mark.parametrize("rate", ["-0.1", "1.1", "2", "abc"])
def test_sample_rate_outside_unit_interval_is_rejected(
    monkeypatch: pytest.MonkeyPatch, base_env: None, rate: str
) -> None:
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_SAMPLE_RATE", rate)

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert "ROOTCAUSE_LANGFUSE_SAMPLE_RATE" in str(caught.value)


@pytest.mark.parametrize("rate", [0.0, 0.5, 1.0])
def test_sample_rate_boundaries_are_accepted(rate: float) -> None:
    settings = Settings(_env_file=None, database_url=DB, langfuse_sample_rate=rate)

    assert settings.langfuse_sample_rate == rate


def test_unknown_prompt_source_is_rejected(monkeypatch: pytest.MonkeyPatch, base_env: None) -> None:
    monkeypatch.setenv("ROOTCAUSE_PROMPT_SOURCE", "database")

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert "ROOTCAUSE_PROMPT_SOURCE" in str(caught.value)


def test_negative_prompt_cache_ttl_is_rejected(
    monkeypatch: pytest.MonkeyPatch, base_env: None
) -> None:
    monkeypatch.setenv("ROOTCAUSE_PROMPT_CACHE_TTL_S", "-1")

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert "ROOTCAUSE_PROMPT_CACHE_TTL_S" in str(caught.value)


def test_malformed_host_is_rejected_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, base_env: None
) -> None:
    monkeypatch.setenv("ROOTCAUSE_LANGFUSE_HOST", "not a url SENTINEL-HOST")

    with pytest.raises(ConfigurationError) as caught:
        get_settings()

    assert HOST_VARIABLE in str(caught.value)
    assert "SENTINEL-HOST" not in str(caught.value)


def test_settings_stay_frozen() -> None:
    settings: Any = Settings(_env_file=None, database_url=DB)

    with pytest.raises(ValueError):  # noqa: PT011 - pydantic frozen-instance error
        settings.langfuse_enabled = True
