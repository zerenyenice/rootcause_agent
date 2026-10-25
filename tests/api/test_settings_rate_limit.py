"""Settings.rate_limit default and environment loading."""

import pytest

from rootcause.errors import ConfigurationError
from rootcause.settings import Settings, clear_settings_cache, get_settings

DB_URL = "postgresql://user:pw@localhost:5432/rootcause"


@pytest.mark.unit
def test_rate_limit_defaults_to_120_per_minute() -> None:
    settings = Settings(database_url=DB_URL, _env_file=None)

    assert settings.rate_limit == "120/minute"


@pytest.mark.unit
def test_rate_limit_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_RATE_LIMIT", "5/second")

    settings = Settings(database_url=DB_URL, _env_file=None)

    assert settings.rate_limit == "5/second"


@pytest.mark.unit
@pytest.mark.parametrize("value", ["0/minute", "abc"])
def test_invalid_rate_limit_is_rejected_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", DB_URL)
    monkeypatch.setenv("ROOTCAUSE_RATE_LIMIT", value)
    clear_settings_cache()

    with pytest.raises(ConfigurationError) as raised:
        get_settings()

    assert "ROOTCAUSE_RATE_LIMIT" in str(raised.value)
    assert "ROOTCAUSE_RATE_LIMIT" in raised.value.variables


@pytest.mark.unit
def test_non_ascii_digit_rate_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", DB_URL)
    monkeypatch.setenv("ROOTCAUSE_RATE_LIMIT", "٣/minute")
    clear_settings_cache()

    with pytest.raises(ConfigurationError) as raised:
        get_settings()

    assert "ROOTCAUSE_RATE_LIMIT" in raised.value.variables
