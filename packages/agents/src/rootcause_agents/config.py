"""Model identifiers used by the orchestrator and (later) its subagents."""

from dataclasses import dataclass

from rootcause_core.settings import Settings


@dataclass(frozen=True)
class AgentModels:
    """Chat-model ids (``provider:model``) per role."""

    orchestrator: str
    fast: str
    reasoning: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "AgentModels":
        return cls(
            orchestrator=settings.orchestrator_model,
            fast=settings.fast_model,
            reasoning=settings.reasoning_model,
        )
