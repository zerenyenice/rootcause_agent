"""Settings.api_allowed_origins: default, environment loading and validation."""

import pytest
from pydantic import ValidationError

from rootcause.settings import Settings

DB_URL = "postgresql://user:pw@localhost:5432/rootcause"

pytestmark = pytest.mark.unit


def test_api_allowed_origins_defaults_to_the_local_web_ui() -> None:
    settings = Settings(database_url=DB_URL, _env_file=None)

    assert settings.api_allowed_origins == ("http://localhost:3000",)


def test_api_allowed_origins_loaded_from_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ROOTCAUSE_API_ALLOWED_ORIGINS", '["https://ui.example.com","http://localhost:3001"]'
    )

    settings = Settings(database_url=DB_URL, _env_file=None)

    assert settings.api_allowed_origins == ("https://ui.example.com", "http://localhost:3001")


@pytest.mark.parametrize(
    "bad",
    ["*", "http://localhost:3000/", "http://localhost:3000/app", "localhost:3000", "ftp://h", ""],
)
def test_invalid_api_allowed_origins_are_rejected(bad: str) -> None:
    with pytest.raises(ValidationError, match="api_allowed_origins"):
        Settings(database_url=DB_URL, api_allowed_origins=(bad,), _env_file=None)
