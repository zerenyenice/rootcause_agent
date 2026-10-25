"""T030: orchestrator system prompt."""

import pytest


@pytest.mark.unit
def test_orchestrator_prompt_contains_template_and_rules() -> None:
    from rootcause_agents.prompts import load_orchestrator_prompt

    prompt = load_orchestrator_prompt()
    assert isinstance(prompt, str)
    for field in ("What:", "When:", "Where:", "Impact:", "write_todos", "executed query"):
        assert field in prompt


@pytest.mark.unit
def test_orchestrator_prompt_states_safety_rules() -> None:
    from rootcause_agents.prompts import load_orchestrator_prompt

    prompt = load_orchestrator_prompt()
    lowered = prompt.lower()
    assert "/memories/" in prompt
    assert "personal data" in lowered
    assert "individuals" in lowered
    assert "unverified" in lowered
    assert "SELECT" in prompt
    assert "WITH" in prompt


def _langfuse_prompt(version: int = 11) -> object:
    from langfuse.api import Prompt_Text
    from langfuse.model import TextPromptClient

    return TextPromptClient(
        Prompt_Text(
            name="orchestrator",
            version=version,
            prompt="REMOTE PROMPT (frame the problem; write_todos)",
            type="text",
            config={},
            labels=["production"],
            tags=[],
        )
    )


@pytest.mark.unit
def test_prompt_comes_from_langfuse_when_the_source_is_langfuse(
    monkeypatch: pytest.MonkeyPatch, langfuse_capture: object, langfuse_env: object
) -> None:
    from langfuse import Langfuse

    from rootcause_agents.prompts import load_orchestrator_prompt

    monkeypatch.setenv("ROOTCAUSE_PROMPT_SOURCE", "langfuse")
    monkeypatch.setattr(Langfuse, "get_prompt", lambda self, name, **kw: _langfuse_prompt())

    assert load_orchestrator_prompt() == "REMOTE PROMPT (frame the problem; write_todos)"


@pytest.mark.unit
def test_prompt_falls_back_to_the_packaged_file_when_langfuse_fails(
    monkeypatch: pytest.MonkeyPatch, langfuse_capture: object, langfuse_env: object
) -> None:
    from langfuse import Langfuse

    from rootcause_agents.prompts import load_orchestrator_prompt

    def boom(self: object, name: str, **kwargs: object) -> object:
        raise ConnectionError("langfuse is down")

    monkeypatch.setenv("ROOTCAUSE_PROMPT_SOURCE", "langfuse")
    monkeypatch.setattr(Langfuse, "get_prompt", boom)

    prompt = load_orchestrator_prompt()

    assert "What:" in prompt
    assert "write_todos" in prompt
    assert "REMOTE" not in prompt


@pytest.mark.unit
def test_orchestrator_uses_the_langfuse_prompt_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    langfuse_capture: object,
    langfuse_env: object,
    scripted_model: object,
) -> None:
    from deepagents import create_deep_agent
    from langfuse import Langfuse
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    import rootcause_agents.orchestrator as orchestrator
    from rootcause_core.settings import get_settings

    monkeypatch.setenv("ROOTCAUSE_PROMPT_SOURCE", "langfuse")
    monkeypatch.setattr(Langfuse, "get_prompt", lambda self, name, **kw: _langfuse_prompt())
    seen: list[str] = []
    original = create_deep_agent

    def spy(**kwargs: object) -> object:
        seen.append(str(kwargs["system_prompt"]))
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(orchestrator, "create_deep_agent", spy)

    orchestrator.build_orchestrator(
        model=scripted_model(),  # type: ignore[operator]
        settings=get_settings(),
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
    )

    assert seen == ["REMOTE PROMPT (frame the problem; write_todos)"]
