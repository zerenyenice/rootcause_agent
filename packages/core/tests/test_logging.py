"""Tests for rootcause_core.logging."""

import json
import re
from typing import Any, cast

import pytest

from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit


def _settings(monkeypatch: pytest.MonkeyPatch, database_url: str, **env: str) -> Settings:
    from rootcause_core.settings import get_settings

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", database_url)
    for key, value in env.items():
        monkeypatch.setenv(f"ROOTCAUSE_{key.upper()}", value)
    return get_settings()


def _output(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def _last_json_line(output: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.strip()]
    assert lines, "no log output captured"
    parsed = json.loads(lines[-1])
    assert isinstance(parsed, dict)
    return cast("dict[str, Any]", parsed)


def test_json_mode_emits_valid_json_with_standard_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database_url: str
) -> None:
    from rootcause_core.logging import configure_logging, get_logger

    configure_logging(_settings(monkeypatch, database_url, log_format="json"))

    get_logger("x").info("hello", key="v")
    record = _last_json_line(_output(capsys))

    assert record["event"] == "hello"
    assert record["level"] == "info"
    assert record["key"] == "v"
    assert record["logger"] == "x"
    assert record["timestamp"]


def test_json_mode_without_active_span_has_no_trace_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database_url: str
) -> None:
    from rootcause_core.logging import configure_logging, get_logger

    configure_logging(_settings(monkeypatch, database_url, log_format="json"))

    get_logger("x").info("no-span")
    record = _last_json_line(_output(capsys))

    assert "trace_id" not in record
    assert "span_id" not in record


def test_console_mode_output_is_not_json_but_contains_event(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database_url: str
) -> None:
    from rootcause_core.logging import configure_logging, get_logger

    configure_logging(_settings(monkeypatch, database_url, log_format="console"))

    get_logger("x").info("hello", key="v")
    output = _output(capsys)

    assert "hello" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.strip().splitlines()[-1])


def test_log_line_inside_active_span_carries_trace_and_span_ids(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database_url: str
) -> None:
    from rootcause_core.logging import configure_logging, get_logger
    from rootcause_core.telemetry import configure_telemetry, get_tracer

    settings = _settings(monkeypatch, database_url, log_format="json")
    configure_telemetry(settings)
    configure_logging(settings)

    with get_tracer("t").start_as_current_span("s") as span:
        get_logger("x").info("inside")
        context = span.get_span_context()
        record = _last_json_line(_output(capsys))

    assert record["trace_id"] == format(context.trace_id, "032x")
    assert record["span_id"] == format(context.span_id, "016x")
    assert re.fullmatch(r"[0-9a-f]{32}", record["trace_id"])
    assert re.fullmatch(r"[0-9a-f]{16}", record["span_id"])


def test_unicode_and_special_characters_survive_json_rendering(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database_url: str
) -> None:
    from rootcause_core.logging import configure_logging, get_logger

    configure_logging(_settings(monkeypatch, database_url, log_format="json"))
    event = "café \U0001f600 '; DROP TABLE x;--"

    get_logger("x").info(event, note="line1\nline2")
    record = _last_json_line(_output(capsys))

    assert record["event"] == event
    assert record["note"] == "line1\nline2"


def test_log_level_filters_lower_severity_messages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database_url: str
) -> None:
    from rootcause_core.logging import configure_logging, get_logger

    configure_logging(_settings(monkeypatch, database_url, log_format="json", log_level="ERROR"))

    get_logger("x").info("quiet")
    get_logger("x").error("loud")
    output = _output(capsys)

    assert "quiet" not in output
    assert "loud" in output


def test_stdlib_logger_extra_fields_are_rendered_in_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database_url: str
) -> None:
    import logging

    from rootcause_core.logging import configure_logging

    configure_logging(_settings(monkeypatch, database_url, log_format="json"))

    logging.getLogger("x").info("hi", extra={"request_id": "r1"})
    record = _last_json_line(_output(capsys))

    assert record["request_id"] == "r1"
