"""Orchestrator construction (no graph is created at import time)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain.chat_models import init_chat_model
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore

from rootcause_agents.backends import build_backend, build_checkpointer
from rootcause_agents.config import AgentModels
from rootcause_agents.prompts import load_orchestrator_prompt
from rootcause_agents.tools.sql import (
    ApprovalRegistry,
    QueryExecutor,
    make_sql_tools,
)
from rootcause_core.settings import Settings, get_settings


@dataclass(frozen=True)
class OrchestratorHandle:
    """Compiled graph plus the stateful collaborators the API must manage."""

    graph: Any
    store: BaseStore | None
    checkpointer: Any


def _approval_config(
    executor: QueryExecutor, threshold: int, registry: ApprovalRegistry
) -> InterruptOnConfig:
    def needs_approval(request: Any) -> bool:
        sql = request.tool_call["args"].get("sql", "")
        try:
            expensive = executor.explain(sql).estimated_rows > threshold
        except Exception:  # noqa: BLE001 - run_sql reports the failure as a payload
            return False
        if expensive:
            registry.mark(sql)
        return expensive

    return InterruptOnConfig(allowed_decisions=["approve", "reject"], when=needs_approval)


def build_orchestrator(
    *,
    model: Any = None,
    settings: Settings | None = None,
    store: BaseStore | None = None,
    checkpointer: Any = None,
    executor: QueryExecutor | None = None,
    extra_tools: Sequence[BaseTool] = (),
) -> OrchestratorHandle:
    """Build the orchestrator; pass ``model`` to avoid creating a real chat model."""
    resolved = settings if settings is not None else get_settings()
    chat_model = (
        model
        if model is not None
        else init_chat_model(AgentModels.from_settings(resolved).orchestrator)
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
