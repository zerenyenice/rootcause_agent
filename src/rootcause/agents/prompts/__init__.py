"""System prompts shipped as package data, resolved through the prompt registry."""

from functools import lru_cache
from importlib import resources

from rootcause.errors import ConfigurationError
from rootcause.prompts import PromptRegistry
from rootcause.settings import Settings, get_settings

ORCHESTRATOR_PROMPT = "orchestrator"
# Every prompt shipped with the package; scripts/push_prompts.py uploads these.
PROMPT_NAMES: tuple[str, ...] = (ORCHESTRATOR_PROMPT,)


def load_packaged_prompt(name: str) -> str:
    """Return the text of the packaged prompt file ``<name>.md``.

    Raises:
        FileNotFoundError: no such prompt is packaged.
    """
    if name not in PROMPT_NAMES:
        raise FileNotFoundError(f"no packaged prompt named {name!r}")
    return resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")


@lru_cache(maxsize=8)
def _registry(settings: Settings) -> PromptRegistry:
    """One registry per settings, so the fallback warning is logged once per process."""
    return PromptRegistry(settings=settings, file_loader=load_packaged_prompt)


def load_orchestrator_prompt(settings: Settings | None = None) -> str:
    """Return the orchestrator system prompt (Langfuse or packaged file, see PromptRegistry).

    Without valid settings (for example in tooling that has no environment) the
    packaged file is returned.
    """
    if settings is None:
        try:
            settings = get_settings()
        except ConfigurationError:
            return load_packaged_prompt(ORCHESTRATOR_PROMPT)
    return _registry(settings).get(ORCHESTRATOR_PROMPT).text
