"""Settings.rate_limit default and environment loading."""

import pytest

from rootcause_core.settings import Settings

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
