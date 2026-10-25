"""Orchestrator construction (no graph is created at import time)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from rootcause_agents.backends import build_backend, build_checkpointer
from rootcause_agents.config import AgentModels
from rootcause_agents.prompts import load_orchestrator_prompt
from rootcause_agents.tools.sql import (
    ApprovalDecision,
    ApprovalRegistry,
    QueryExecutor,
    make_sql_tools,
    thread_id_of,
)
from rootcause_core.errors import ConfigurationError
from rootcause_core.settings import Environment, Settings, get_settings

MODEL_TIMEOUT_SECONDS = 60
MODEL_MAX_RETRIES = 2
MODEL_MAX_TOKENS = 8192


@dataclass(frozen=True)
class OrchestratorHandle:
    """Compiled graph plus the stateful collaborators the API must manage."""

    graph: CompiledStateGraph[Any, Any, Any, Any]
    store: BaseStore | None
    checkpointer: BaseCheckpointSaver[Any]


def _approval_config(
    executor: QueryExecutor, threshold: int, registry: ApprovalRegistry
) -> InterruptOnConfig:
    def evaluate(sql: str) -> ApprovalDecision:
        try:
            estimate = executor.explain(sql)
        except Exception:  # noqa: BLE001 - fail closed: a human decides when unsure
            return ApprovalDecision(requires_approval=True, estimate=None)
        return ApprovalDecision(estimate.estimated_rows > threshold, estimate)

    def needs_approval(request: Any) -> bool:
        key = (
            thread_id_of(request.runtime.config),
            str(request.tool_call["id"]),
        )
        sql = request.tool_call["args"].get("sql", "")
        return registry.decide(key, lambda: evaluate(sql)).requires_approval

    return InterruptOnConfig(allowed_decisions=["approve", "reject"], when=needs_approval)


def build_orchestrator(
    *,
    model: BaseChatModel | None = None,
    settings: Settings | None = None,
    store: BaseStore | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    executor: QueryExecutor | None = None,
    extra_tools: Sequence[BaseTool] = (),
) -> OrchestratorHandle:
    """Build the orchestrator; pass ``model`` to avoid creating a real chat model."""
    resolved = settings if settings is not None else get_settings()
    if resolved.environment is Environment.PROD and (checkpointer is None or store is None):
        raise ConfigurationError(
            "A durable checkpointer and store are required in prod; "
            "open postgres_checkpointer_cm in the app lifespan and pass both."
        )
    chat_model = (
        model
        if model is not None
        else init_chat_model(
            AgentModels.from_settings(resolved).orchestrator,
            timeout=MODEL_TIMEOUT_SECONDS,
            max_retries=MODEL_MAX_RETRIES,
            max_tokens=MODEL_MAX_TOKENS,
        )
    )
    saver = checkpointer if checkpointer is not None else build_checkpointer(resolved)

    tools: list[BaseTool] = []
    interrupt_on: dict[str, bool | InterruptOnConfig] = {}
    if executor is not None:
        threshold = resolved.scan_row_threshold
        registry = ApprovalRegistry()
        tools.extend(make_sql_tools(executor, threshold, registry))
        interrupt_on["run_sql"] = _approval_config(executor, threshold, registry)

    graph = create_deep_agent(
        model=chat_model,
        tools=[*tools, *extra_tools],
        system_prompt=load_orchestrator_prompt(),
        middleware=[TodoListMiddleware()],
        backend=build_backend(store),
        interrupt_on=interrupt_on,
        checkpointer=saver,
        store=store,
    )
    return OrchestratorHandle(graph=graph, store=store, checkpointer=saver)
