"""T009: scripts/push_prompts.py uploads the packaged prompts to Langfuse (dry-run is offline)."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "push_prompts.py"
PROMPT_FILE = (
    Path(__file__).resolve().parents[3]
    / "packages/agents/src/rootcause_agents/prompts/orchestrator.md"
)


def _load_script() -> ModuleType:
    assert SCRIPT.is_file(), "scripts/push_prompts.py is missing"
    spec = importlib.util.spec_from_file_location("push_prompts_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api_calls(monkeypatch: pytest.MonkeyPatch, langfuse_capture: Any) -> list[dict[str, Any]]:
    from langfuse import Langfuse

    calls: list[dict[str, Any]] = []

    def create_prompt(self: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(Langfuse, "create_prompt", create_prompt)
    return calls


def test_dry_run_prints_prompt_names_and_makes_no_api_call(
    api_calls: list[dict[str, Any]],
    langfuse_capture: Any,
    langfuse_env: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _load_script().main(["--dry-run"])

    assert exit_code == 0
    assert "orchestrator" in capsys.readouterr().out
    assert api_calls == []


def test_dry_run_does_not_need_credentials_or_a_client(
    base_env: None, langfuse_capture: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = _load_script().main(["--dry-run"])

    assert exit_code == 0
    assert "orchestrator" in capsys.readouterr().out
    assert langfuse_capture.init_kwargs == []


def test_dry_run_is_the_default(
    api_calls: list[dict[str, Any]], langfuse_env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = _load_script().main([])

    assert exit_code == 0
    assert "orchestrator" in capsys.readouterr().out
    assert api_calls == []


def test_dry_run_never_prints_secret_values(
    api_calls: list[dict[str, Any]], langfuse_env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _load_script().main(["--dry-run"])

    captured = capsys.readouterr()
    assert "SENTINEL" not in captured.out + captured.err


def test_apply_uploads_each_prompt_once_with_its_file_text(
    api_calls: list[dict[str, Any]], langfuse_env: Any
) -> None:
    exit_code = _load_script().main(["--apply"])

    assert exit_code == 0
    [call] = api_calls
    assert call["name"] == "orchestrator"
    assert call["prompt"] == PROMPT_FILE.read_text(encoding="utf-8")
    assert call.get("type", "text") == "text"


def test_apply_without_credentials_fails_without_a_traceback(
    base_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = _load_script().main(["--apply"])

    assert exit_code != 0
    assert "ROOTCAUSE_LANGFUSE" in capsys.readouterr().err


def test_unknown_flag_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        _load_script().main(["--bogus"])

    assert caught.value.code != 0
