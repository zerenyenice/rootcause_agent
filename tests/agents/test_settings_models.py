"""T030: model-id and scan-threshold settings."""

import pytest
from pydantic import ValidationError

from rootcause.settings import Settings

DB_URL = "postgresql://user:pw@localhost:5432/rootcause"


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, database_url=DB_URL, **overrides)  # type: ignore[arg-type]


@pytest.mark.unit
def test_model_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ORCHESTRATOR_MODEL", "FAST_MODEL", "REASONING_MODEL", "SCAN_ROW_THRESHOLD"):
        monkeypatch.delenv(f"ROOTCAUSE_{name}", raising=False)
    settings = make()
    assert settings.orchestrator_model == "anthropic:claude-opus-5"
    assert settings.fast_model == "anthropic:claude-sonnet-5"
    assert settings.reasoning_model == "anthropic:claude-opus-5"
    assert settings.scan_row_threshold == 5_000_000


@pytest.mark.unit
def test_model_and_threshold_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_ORCHESTRATOR_MODEL", "openai:gpt-x")
    monkeypatch.setenv("ROOTCAUSE_FAST_MODEL", "openai:fast")
    monkeypatch.setenv("ROOTCAUSE_REASONING_MODEL", "openai:think")
    monkeypatch.setenv("ROOTCAUSE_SCAN_ROW_THRESHOLD", "123")
    settings = make()
    assert settings.orchestrator_model == "openai:gpt-x"
    assert settings.fast_model == "openai:fast"
    assert settings.reasoning_model == "openai:think"
    assert settings.scan_row_threshold == 123


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, -1])
def test_scan_row_threshold_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        make(scan_row_threshold=value)


@pytest.mark.unit
def test_scan_row_threshold_rejects_non_integer() -> None:
    with pytest.raises(ValidationError):
        make(scan_row_threshold="many")


@pytest.mark.unit
def test_scan_row_threshold_accepts_boundary_one() -> None:
    assert make(scan_row_threshold=1).scan_row_threshold == 1


@pytest.mark.unit
def test_agent_models_dataclass_is_removed() -> None:
    import importlib

    try:
        module = importlib.import_module("rootcause.agents.config")
    except ModuleNotFoundError:
        return
    assert not hasattr(module, "AgentModels")
