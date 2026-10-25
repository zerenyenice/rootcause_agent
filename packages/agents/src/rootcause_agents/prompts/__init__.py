"""System prompts shipped as package data."""

from importlib import resources


def load_orchestrator_prompt() -> str:
    """Return the orchestrator system prompt text."""
    return resources.files(__package__).joinpath("orchestrator.md").read_text(encoding="utf-8")
