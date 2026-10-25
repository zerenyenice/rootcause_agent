"""T030: orchestrator system prompt."""

import pytest


@pytest.mark.unit
def test_orchestrator_prompt_contains_template_and_rules() -> None:
    from rootcause_agents.prompts import load_orchestrator_prompt

    prompt = load_orchestrator_prompt()
    assert isinstance(prompt, str)
    for field in ("What:", "When:", "Where:", "Impact:", "write_todos", "executed query"):
        assert field in prompt
