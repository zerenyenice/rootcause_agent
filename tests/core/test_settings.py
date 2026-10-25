"""Tests for rootcause.settings."""

from pathlib import Path

import pytest

from rootcause.settings import Settings

pytestmark = pytest.mark.unit


def _load() -> Settings:
    from rootcause.settings import get_settings

    return get_settings()


def test_loads_all_fields_from_prefixed_env_vars(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "prod")
    monkeypatch.setenv("ROOTCAUSE_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ROOTCAUSE_LOG_FORMAT", "console")
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", database_url)
    monkeypatch.setenv("ROOTCAUSE_OPENVIKING_URL", "http://viking.local:1933/")
    monkeypatch.setenv("ROOTCAUSE_OTEL_EXPORTER", "otlp")
    monkeypatch.setenv("ROOTCAUSE_OTEL_ENDPOINT", "http://otel:4318")
    monkeypatch.setenv("ROOTCAUSE_SERVICE_NAME", "analyzer")

    settings = _load()

    assert settings.environment == "prod"
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "console"
    assert settings.database_url.get_secret_value() == database_url
    assert str(settings.openviking_url).startswith("http://viking.local:1933")
    assert settings.otel_exporter == "otlp"
    assert str(settings.otel_endpoint).rstrip("/") == "http://otel:4318"
    assert settings.service_name == "analyzer"


@pytest.mark.usefixtures("base_env")
def test_defaults_apply_when_only_database_url_is_set() -> None:
    settings = _load()

    assert settings.environment == "dev"
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.otel_exporter == "none"
    assert settings.service_name == "rootcause"
    assert settings.openviking_url is None
    assert settings.otel_endpoint is None


def test_missing_database_url_raises_configuration_error_naming_variable() -> None:
    from rootcause.errors import ConfigurationError

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    assert "ROOTCAUSE_DATABASE_URL" in str(excinfo.value)


def test_configuration_error_message_does_not_leak_env_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rootcause.errors import ConfigurationError

    monkeypatch.setenv("ROOTCAUSE_SERVICE_NAME", "leaky-service-name-123")
    monkeypatch.setenv("ROOTCAUSE_LOG_FORMAT", "bogus-format-value-456")

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    message = str(excinfo.value)
    assert "leaky-service-name-123" not in message
    assert "bogus-format-value-456" not in message


def test_configuration_error_is_a_rootcause_error() -> None:
    from rootcause.errors import ConfigurationError, RootCauseError

    assert issubclass(ConfigurationError, RootCauseError)


@pytest.mark.usefixtures("base_env")
def test_database_url_is_secret_and_masked_in_repr_and_str(database_url: str) -> None:
    from pydantic import SecretStr

    settings = _load()

    assert isinstance(settings.database_url, SecretStr)
    for rendered in (repr(settings), str(settings), repr(settings.database_url)):
        assert "s3cr3t-pw" not in rendered
        assert database_url not in rendered


@pytest.mark.usefixtures("base_env")
@pytest.mark.parametrize(
    ("variable", "bad_value"),
    [
        ("ROOTCAUSE_ENVIRONMENT", "staging"),
        ("ROOTCAUSE_LOG_FORMAT", "xml"),
        ("ROOTCAUSE_OTEL_EXPORTER", "zipkin"),
    ],
)
def test_invalid_enum_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, variable: str, bad_value: str
) -> None:
    from rootcause.errors import ConfigurationError

    monkeypatch.setenv(variable, bad_value)

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    assert bad_value not in str(excinfo.value)


@pytest.mark.usefixtures("base_env")
def test_get_settings_returns_cached_instance() -> None:
    assert _load() is _load()


@pytest.mark.usefixtures("base_env")
def test_clear_settings_cache_forces_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    from rootcause.settings import clear_settings_cache

    first = _load()
    monkeypatch.setenv("ROOTCAUSE_SERVICE_NAME", "reloaded")
    assert _load() is first  # still cached

    clear_settings_cache()
    second = _load()

    assert second is not first
    assert second.service_name == "reloaded"


@pytest.mark.usefixtures("base_env")
def test_settings_are_frozen() -> None:
    settings = _load()

    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - pydantic ValidationError
        settings.service_name = "mutated"  # type: ignore[misc]  # intentional: asserting frozen model raises

    assert type(excinfo.value).__name__ == "ValidationError"
    assert settings.service_name == "rootcause"


def test_empty_database_url_is_treated_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rootcause.errors import ConfigurationError

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "")

    with pytest.raises(ConfigurationError):
        _load()


@pytest.mark.usefixtures("base_env")
def test_log_level_is_case_insensitive_and_normalised_to_upper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROOTCAUSE_LOG_LEVEL", "info")

    settings = _load()

    assert settings.log_level == "INFO"


@pytest.mark.usefixtures("base_env")
def test_invalid_otel_endpoint_raises_configuration_error_naming_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rootcause.errors import ConfigurationError

    monkeypatch.setenv("ROOTCAUSE_OTEL_ENDPOINT", "not-a-url")

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    assert "ROOTCAUSE_OTEL_ENDPOINT" in str(excinfo.value)


@pytest.mark.usefixtures("base_env")
def test_valid_otel_endpoint_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_OTEL_ENDPOINT", "http://otel:4318")

    settings = _load()

    assert str(settings.otel_endpoint).rstrip("/") == "http://otel:4318"


def test_dotenv_file_is_ignored_in_prod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, database_url: str
) -> None:
    from rootcause.errors import ConfigurationError

    (tmp_path / ".env").write_text(f"ROOTCAUSE_DATABASE_URL={database_url}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "prod")

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    assert "ROOTCAUSE_DATABASE_URL" in str(excinfo.value)


def test_dotenv_file_is_honoured_in_dev(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, database_url: str
) -> None:
    (tmp_path / ".env").write_text(f"ROOTCAUSE_DATABASE_URL={database_url}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "dev")

    settings = _load()

    assert settings.database_url.get_secret_value() == database_url


def test_configuration_error_does_not_retain_pydantic_error_on_chain() -> None:
    from rootcause.errors import ConfigurationError

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_prod_declared_only_in_dotenv_is_rejected_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rootcause.errors import ConfigurationError

    (tmp_path / ".env").write_text(
        "ROOTCAUSE_ENVIRONMENT=prod\nROOTCAUSE_DATABASE_URL=postgresql://u:p@h/db\n"
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    message = str(excinfo.value)
    assert "ROOTCAUSE_ENVIRONMENT" in message
    assert ".env" in message
    assert "postgresql://u:p@h/db" not in message
    assert "u:p@h" not in message


@pytest.mark.parametrize("raw", ["PROD", "Prod", "prod"])
def test_environment_prod_is_case_insensitive_and_ignores_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, database_url: str, raw: str
) -> None:
    from rootcause.settings import Environment

    (tmp_path / ".env").write_text("ROOTCAUSE_SERVICE_NAME=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", raw)
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", database_url)

    settings = _load()

    assert settings.environment is Environment.PROD
    assert settings.service_name == "rootcause"


@pytest.mark.usefixtures("base_env")
@pytest.mark.parametrize("raw", ["Dev", "DEV"])
def test_environment_dev_is_case_insensitive_and_normalised(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    from rootcause.settings import Environment

    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", raw)

    settings = _load()

    assert settings.environment is Environment.DEV
