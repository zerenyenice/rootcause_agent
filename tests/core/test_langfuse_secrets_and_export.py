"""T009 amendment: every secret setting and the exporter wrapper keep credentials and PII out.

Secrets are discovered from ``Settings`` itself: adding a ``SecretStr`` field without
feeding it to the redactors fails these tests.
"""

import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from opentelemetry.trace import Status, StatusCode
from pydantic import SecretStr

from rootcause.settings import Settings

pytestmark = pytest.mark.unit

DB_PASSWORD = "DBPW-SENTINEL-4711"  # gitleaks:allow
SECRET_VALUES = {
    "anthropic_api_key": "sk-ant-api03-ANTHROPICSENTINEL",
    "azure_openai_api_key": "AZURESENTINEL0123456789",
    "embedding_api_key": "EMBEDDINGSENTINEL0123456789",
    "openviking_api_key": "OPENVIKINGSENTINEL0123456789",
    "langfuse_secret_key": "sk-lf-LFSECRETSENTINEL",
    "secret_key": "cm9vdGNhdXNlLXRlc3QtZmVybmV0LWtleS0zMmJ5dCE=",  # gitleaks:allow
}
MIGRATION_PASSWORD = "MIGPW-SENTINEL-5821"  # gitleaks:allow
ADMIN_PASSWORD = "ADMPW-SENTINEL-9937"  # gitleaks:allow
EMAIL = "export.sentinel@example.com"


def _secret_fields() -> list[str]:
    return [
        name
        for name, field in Settings.model_fields.items()
        if SecretStr in getattr(field.annotation, "__args__", (field.annotation,))
    ]


def _all_secrets_settings(**overrides: Any) -> tuple[Settings, dict[str, str]]:
    values: dict[str, str] = {
        "database_url": f"postgresql://svc:{DB_PASSWORD}@db.internal:5432/rc",
        "migration_database_url": (  # gitleaks:allow
            f"postgresql://migrator:{MIGRATION_PASSWORD}@db.internal:5432/rc"  # gitleaks:allow
        ),
        "admin_database_url": (  # gitleaks:allow
            f"postgresql://admin:{ADMIN_PASSWORD}@db.internal:5432/rc"  # gitleaks:allow
        ),
        "langfuse_public_key": f"pk-lf-PUBSENTINEL-{uuid.uuid4().hex[:8]}",
        **SECRET_VALUES,
    }
    settings = Settings(
        _env_file=None,
        environment="test",
        langfuse_enabled=True,
        langfuse_host="http://127.0.0.1:9",
        azure_openai_endpoint="https://example.openai.azure.com",
        embedding_api_base="http://embeddings.local/v1",
        openviking_url="http://viking.local:1933",
        **{**values, **overrides},
    )
    return settings, values


def _raw(settings: Settings, name: str) -> str:
    value = getattr(settings, name)
    assert isinstance(value, SecretStr)
    return value.get_secret_value()


def test_the_secret_field_discovery_covers_the_known_credentials() -> None:
    assert set(_secret_fields()) >= {
        "database_url",
        "migration_database_url",
        "admin_database_url",
        "anthropic_api_key",
        "azure_openai_api_key",
        "embedding_api_key",
        "openviking_api_key",
        "langfuse_public_key",
        "langfuse_secret_key",
    }


def test_every_secret_setting_is_redacted_by_the_trace_mask(
    langfuse_capture: Any,
) -> None:
    from rootcause.clients.langfuse import langfuse_client

    settings, _ = _all_secrets_settings()
    langfuse_client(settings)
    mask = langfuse_capture.init_kwargs[-1]["mask"]

    for name in _secret_fields():
        raw = _raw(settings, name)
        masked = json.dumps(
            mask(data={"messages": [{"role": "user", "content": f"leak {raw} end"}], "k": raw})
        )
        assert raw not in masked, name
    url_masked = mask(data=f"dsn {_raw(settings, 'database_url')}")
    assert DB_PASSWORD not in url_masked
    for name, password in (
        ("migration_database_url", MIGRATION_PASSWORD),
        ("admin_database_url", ADMIN_PASSWORD),
    ):
        assert password not in mask(data=f"dsn {_raw(settings, name)}"), name


def test_every_secret_setting_is_redacted_from_exported_spans(langfuse_capture: Any) -> None:
    from rootcause.clients.langfuse import flush_langfuse, langfuse_client

    settings, _ = _all_secrets_settings()
    client = langfuse_client(settings)
    assert client is not None
    raws = [_raw(settings, name) for name in _secret_fields()] + [
        DB_PASSWORD,
        MIGRATION_PASSWORD,
        ADMIN_PASSWORD,
    ]
    with client.start_as_current_observation(name="probe") as span:
        for index, raw in enumerate(raws):
            span._otel_span.set_attribute(f"custom.attr{index}", f"value {raw} tail")  # noqa: SLF001
        span._otel_span.set_status(Status(StatusCode.ERROR, f"failed with {' '.join(raws)}"))  # noqa: SLF001
    flush_langfuse()

    [exported] = [s for s in langfuse_capture.exporter.get_finished_spans() if s.name == "probe"]
    blob = json.dumps(dict(exported.attributes or {}), default=str) + str(
        exported.status.description
    )
    for raw in raws:
        assert raw not in blob


def test_every_secret_setting_is_redacted_from_logs(capsys: pytest.CaptureFixture[str]) -> None:
    from rootcause.logging import configure_logging, get_logger

    settings, _ = _all_secrets_settings()
    configure_logging(settings)
    raws = [_raw(settings, name) for name in _secret_fields()] + [
        DB_PASSWORD,
        MIGRATION_PASSWORD,
        ADMIN_PASSWORD,
    ]

    for raw in raws:
        get_logger("x").error("boom", detail=f"value {raw} tail")
        get_logger("x").error(f"inline {raw}")
    import logging

    for raw in raws:
        logging.getLogger("third.party").warning("lib says %s", raw)
    captured = capsys.readouterr()

    output = captured.out + captured.err
    assert "boom" in output
    for raw in raws:
        assert raw not in output


# --- exporter wrapper ---


def _export_probe(
    capture: Any, settings: Settings, *, attributes: dict[str, Any], status: str
) -> Any:
    from rootcause.clients.langfuse import flush_langfuse, langfuse_client

    client = langfuse_client(settings)
    assert client is not None
    with client.start_as_current_observation(name="wrapper-probe") as span:
        for key, value in attributes.items():
            span._otel_span.set_attribute(key, value)  # noqa: SLF001
        span._otel_span.set_status(Status(StatusCode.ERROR, status))  # noqa: SLF001
        span._otel_span.add_event("exception", {"exception.message": status})  # noqa: SLF001
    flush_langfuse()
    [exported] = [s for s in capture.exporter.get_finished_spans() if s.name == "wrapper-probe"]
    return exported


def test_exporter_redacts_dsn_password_api_key_and_email_everywhere(
    langfuse_capture: Any,
) -> None:
    settings, _ = _all_secrets_settings()
    dsn = _raw(settings, "database_url")
    anthropic = _raw(settings, "anthropic_api_key")
    text = f"failed {dsn} using {anthropic} for {EMAIL}"

    exported = _export_probe(
        langfuse_capture,
        settings,
        attributes={"custom.text": text, "custom.list": [text, "plain"], "exception.message": text},
        status=text,
    )

    blobs = [
        json.dumps(dict(exported.attributes or {}), default=str),
        str(exported.status.description),
        *(json.dumps(dict(event.attributes or {}), default=str) for event in exported.events),
    ]
    assert exported.events, "the probe recorded an event"
    for blob in blobs:
        assert DB_PASSWORD not in blob
        assert anthropic not in blob
        assert EMAIL not in blob
    assert "failed" in str(exported.status.description)


def test_exporter_keeps_ordinary_attributes_and_numbers(langfuse_capture: Any) -> None:
    settings, _ = _all_secrets_settings()

    exported = _export_probe(
        langfuse_capture,
        settings,
        attributes={"custom.count": 20000, "custom.flag": True, "custom.sql": "SELECT 1"},
        status="query timed out",
    )

    assert exported.attributes["custom.count"] == 20000
    assert exported.attributes["custom.flag"] is True
    assert exported.attributes["custom.sql"] == "SELECT 1"
    assert exported.status.description == "query timed out"


def test_exporter_keeps_emails_when_the_pii_pass_is_off(langfuse_capture: Any) -> None:
    settings, _ = _all_secrets_settings(langfuse_mask_pii=False)

    exported = _export_probe(
        langfuse_capture,
        settings,
        attributes={"custom.text": f"mail {EMAIL}"},
        status="x",
    )

    assert EMAIL in str(exported.attributes["custom.text"])


def test_exporter_redacts_resource_attributes(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> None:
    settings = make_langfuse_settings(langfuse_release=f"rel {EMAIL} {DB_PASSWORD}")

    exported = _export_probe(langfuse_capture, settings, attributes={}, status="x")

    resource = json.dumps(dict(exported.resource.attributes), default=str)
    assert EMAIL not in resource
