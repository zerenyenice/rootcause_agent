"""Unattended, bounded investigation: question in, the subagent's WhyChain out.

Used by the evaluation runner, whose contract hands the agent only the question.
Every approval interrupt is rejected (nobody is there to approve an expensive
scan), so the run continues with the agent proposing cheaper queries.
"""

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from pydantic import ValidationError

from rootcause.agents.orchestrator import OrchestratorHandle, build_orchestrator
from rootcause.agents.reports import WhyChain
from rootcause.connections.duckdb_executor import WAREHOUSE_PATH_VARIABLE, executor_from_settings
from rootcause.errors import ConfigurationError
from rootcause.logging import get_logger
from rootcause.settings import get_settings

# Upper bound on auto-rejected approval rounds within one run.
MAX_RESUMES = 20
TASK_TOOL = "task"
_REJECTION_MESSAGE = (
    "Rejected automatically: unattended runs cannot approve large scans. "
    "Use a cheaper query (filters, sampling, aggregates)."
)

_logger = get_logger(__name__)


def investigate(question: str, *, handle: OrchestratorHandle | None = None) -> WhyChain | None:
    """Run one investigation of ``question`` and return the root-cause WhyChain, if any.

    Without ``handle`` the orchestrator is built from settings over the configured
    warehouse. Returns ``None`` for a blank question, when the orchestrator never
    delegated to the root-cause subagent, or when the step budget ran out.

    Raises:
        ConfigurationError: no handle is given and the warehouse is not configured
            or does not exist.
    """
    if not question.strip():
        return None
    if handle is not None:
        return _run(handle, question)
    with _handle_from_settings() as built:
        return _run(built, question)


@contextmanager
def _handle_from_settings() -> Iterator[OrchestratorHandle]:
    """Orchestrator over the configured warehouse; the warehouse closes on exit."""
    from rootcause.evals.manufacturing.table_docs import TABLE_DOCS

    settings = get_settings()
    executor = executor_from_settings(settings)
    if executor is None:
        raise ConfigurationError(
            f"{WAREHOUSE_PATH_VARIABLE} must be set to run an investigation.",
            variables=(WAREHOUSE_PATH_VARIABLE,),
        )
    with executor:
        yield build_orchestrator(
            settings=settings,
            store=InMemoryStore(),
            checkpointer=InMemorySaver(),
            executor=executor,
            table_docs=TABLE_DOCS,
        )


def _run(handle: OrchestratorHandle, question: str) -> WhyChain | None:
    config: RunnableConfig = {
        "configurable": {"thread_id": f"investigate-{uuid.uuid4().hex}"},
        "recursion_limit": handle.settings.investigation_max_steps,
    }
    payload: Any = {"messages": [HumanMessage(question)]}
    try:
        for resumes in range(MAX_RESUMES + 1):
            result = handle.graph.invoke(payload, config)
            pending = result.get("__interrupt__") or ()
            if not pending:
                return _log_outcome(_why_chain(result.get("messages", ())), resumes)
            payload = Command(resume=_reject_all(pending))
    except GraphRecursionError:
        _logger.warning("investigation_step_budget_exhausted", max_steps=config["recursion_limit"])
        return None
    _logger.warning("investigation_resume_budget_exhausted", max_resumes=MAX_RESUMES)
    return _why_chain(result.get("messages", ()))


def _reject_all(interrupts: Sequence[Any]) -> dict[str, Any]:
    """One reject decision per pending action request (parallel calls included)."""
    if len(interrupts) != 1:
        # Several interrupts need resuming by id; a single run never raises more than one.
        return {
            interrupt.id: {"decisions": _rejections(interrupt.value)} for interrupt in interrupts
        }
    return {"decisions": _rejections(interrupts[0].value)}


def _rejections(value: Any) -> list[dict[str, str]]:
    requests = value.get("action_requests", ()) if isinstance(value, dict) else ()
    return [{"type": "reject", "message": _REJECTION_MESSAGE} for _ in requests]


def _why_chain(messages: Sequence[BaseMessage]) -> WhyChain | None:
    """The last WhyChain returned by the ``task`` tool; ``None`` when there is none."""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or message.name != TASK_TOOL:
            continue
        try:
            return WhyChain.model_validate_json(str(message.content))
        except ValidationError:
            continue  # another subagent's free-text answer
    return None


def _log_outcome(chain: WhyChain | None, resumes: int) -> WhyChain | None:
    # Shape only: never the rows, SQL or answers.
    _logger.info(
        "investigation_finished",
        has_chain=chain is not None,
        partial=chain.partial if chain else None,
        levels=len(chain.levels) if chain else 0,
        rejected_rounds=resumes,
    )
    return chain
