"""T009: rootcause.prompts.PromptRegistry (files, Langfuse, fallback)."""

from collections.abc import Callable
from typing import Any

import pytest
from structlog.testing import capture_logs

from rootcause.settings import Settings

pytestmark = pytest.mark.unit

FILE_TEXT = "You are the file prompt."
LANGFUSE_TEXT = "You are the Langfuse prompt."


def _loader(name: str) -> str:
    assert name == "orchestrator"
    return FILE_TEXT


def _registry(settings: Settings) -> Any:
    from rootcause.prompts import PromptRegistry

    return PromptRegistry(settings=settings, file_loader=_loader)


def _text_prompt(version: int = 7, text: str = LANGFUSE_TEXT) -> Any:
    from langfuse.api import Prompt_Text
    from langfuse.model import TextPromptClient

    return TextPromptClient(
        Prompt_Text(
            name="orchestrator",
            version=version,
            prompt=text,
            type="text",
            config={},
            labels=["production"],
            tags=[],
        )
    )


@pytest.fixture
def files_settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql://u:p@localhost/db")


@pytest.fixture
def get_prompt_calls(
    monkeypatch: pytest.MonkeyPatch, langfuse_capture: Any
) -> list[dict[str, Any]]:
    from langfuse import Langfuse

    calls: list[dict[str, Any]] = []

    def get_prompt(self: Any, name: str, **kwargs: Any) -> Any:
        calls.append({"name": name, **kwargs})
        return _text_prompt()

    monkeypatch.setattr(Langfuse, "get_prompt", get_prompt)
    return calls


def test_files_source_returns_the_packaged_prompt(
    files_settings: Settings, langfuse_capture: Any
) -> None:
    template = _registry(files_settings).get("orchestrator")

    assert template.name == "orchestrator"
    assert template.text == FILE_TEXT
    assert template.source == "files"
    assert langfuse_capture.init_kwargs == []


def test_langfuse_source_returns_the_remote_prompt_with_its_version(
    make_langfuse_settings: Callable[..., Settings], get_prompt_calls: list[dict[str, Any]]
) -> None:
    settings = make_langfuse_settings(
        prompt_source="langfuse", prompt_label="staging", prompt_cache_ttl_s=42
    )

    template = _registry(settings).get("orchestrator")

    assert template.name == "orchestrator"
    assert template.text == LANGFUSE_TEXT
    assert template.version == 7
    assert template.source == "langfuse"
    [call] = get_prompt_calls
    assert call["name"] == "orchestrator"
    assert call["label"] == "staging"
    assert call["cache_ttl_seconds"] == 42


def test_langfuse_source_works_even_when_tracing_is_disabled(
    make_langfuse_settings: Callable[..., Settings], get_prompt_calls: list[dict[str, Any]]
) -> None:
    settings = make_langfuse_settings(langfuse_enabled=False, prompt_source="langfuse")

    template = _registry(settings).get("orchestrator")

    assert template.source == "langfuse"
    assert len(get_prompt_calls) == 1


def test_falls_back_to_the_file_when_langfuse_raises(
    make_langfuse_settings: Callable[..., Settings],
    langfuse_capture: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langfuse import Langfuse

    def boom(self: Any, name: str, **kwargs: Any) -> Any:
        raise ConnectionError("langfuse unreachable SENTINEL-HOST")

    monkeypatch.setattr(Langfuse, "get_prompt", boom)
    settings = make_langfuse_settings(prompt_source="langfuse")

    template = _registry(settings).get("orchestrator")

    assert template.text == FILE_TEXT
    assert template.source == "files"


def test_fallback_logs_a_warning_only_once(
    make_langfuse_settings: Callable[..., Settings],
    langfuse_capture: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langfuse import Langfuse

    def boom(self: Any, name: str, **kwargs: Any) -> Any:
        raise TimeoutError("slow")

    monkeypatch.setattr(Langfuse, "get_prompt", boom)
    registry = _registry(make_langfuse_settings(prompt_source="langfuse"))

    with capture_logs() as logs:
        for _ in range(3):
            assert registry.get("orchestrator").source == "files"

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1
    assert "SENTINEL" not in str(warnings[0])


def test_unknown_prompt_name_raises_when_the_file_is_missing(files_settings: Settings) -> None:
    from rootcause.prompts import PromptRegistry

    def missing(name: str) -> str:
        raise FileNotFoundError(name)

    registry = PromptRegistry(settings=files_settings, file_loader=missing)

    with pytest.raises(FileNotFoundError):
        registry.get("nope")


def test_file_text_is_returned_verbatim_including_braces_and_unicode(
    files_settings: Settings,
) -> None:
    from rootcause.prompts import PromptRegistry

    text = "Use {{not_a_variable}} and {braces} \N{GRINNING FACE} é\n"
    registry = PromptRegistry(settings=files_settings, file_loader=lambda _name: text)

    assert registry.get("orchestrator").text == text


def test_prompt_template_is_immutable(files_settings: Settings) -> None:
    template = _registry(files_settings).get("orchestrator")

    with pytest.raises((AttributeError, TypeError, ValueError)):
        template.text = "changed"
