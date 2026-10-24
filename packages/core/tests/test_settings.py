"""Tests for rootcause_core.settings."""

import pytest

pytestmark = pytest.mark.unit


def _load():
    from rootcause_core.settings import get_settings

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
    assert settings.otel_endpoint == "http://otel:4318"
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
    from rootcause_core.errors import ConfigurationError

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    assert "ROOTCAUSE_DATABASE_URL" in str(excinfo.value)


def test_configuration_error_message_does_not_leak_env_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rootcause_core.errors import ConfigurationError

    monkeypatch.setenv("ROOTCAUSE_SERVICE_NAME", "leaky-service-name-123")
    monkeypatch.setenv("ROOTCAUSE_LOG_FORMAT", "bogus-format-value-456")

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    message = str(excinfo.value)
    assert "leaky-service-name-123" not in message
    assert "bogus-format-value-456" not in message


def test_configuration_error_is_a_rootcause_error() -> None:
    from rootcause_core.errors import ConfigurationError, RootCauseError

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
    from rootcause_core.errors import ConfigurationError

    monkeypatch.setenv(variable, bad_value)

    with pytest.raises(ConfigurationError) as excinfo:
        _load()

    assert bad_value not in str(excinfo.value)


@pytest.mark.usefixtures("base_env")
def test_get_settings_returns_cached_instance() -> None:
    assert _load() is _load()


@pytest.mark.usefixtures("base_env")
def test_clear_settings_cache_forces_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    from rootcause_core.settings import clear_settings_cache

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
        settings.service_name = "mutated"  # type: ignore[misc]

    assert type(excinfo.value).__name__ == "ValidationError"
    assert settings.service_name == "rootcause"


def test_empty_database_url_is_treated_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rootcause_core.errors import ConfigurationError

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", "")

    with pytest.raises(ConfigurationError):
        _load()
