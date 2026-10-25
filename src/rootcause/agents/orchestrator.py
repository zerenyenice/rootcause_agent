"""Orchestrator construction (no graph is created at import time)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from rootcause.agents.backends import build_backend, build_checkpointer
from rootcause.agents.prompts import load_orchestrator_prompt
from rootcause.agents.tools.schema import SchemaExecutor, TableDocLike, make_schema_tools
from rootcause.agents.tools.sql import (
    ApprovalDecision,
    ApprovalRegistry,
    QueryExecutor,
    make_sql_tools,
    thread_id_of,
)
from rootcause.clients import chat_model
from rootcause.errors import ConfigurationError
from rootcause.observability.tracing import run_config
from rootcause.settings import Environment, Settings, get_settings


@dataclass(frozen=True)
class OrchestratorHandle:
    """Compiled graph plus the stateful collaborators the API must manage."""

    graph: CompiledStateGraph[Any, Any, Any, Any]
    store: BaseStore | None
    checkpointer: BaseCheckpointSaver[Any]
    settings: Settings

    def config_for(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        tags: Sequence[str] = (),
        tenant_id: str | None = None,
        connection_id: str | None = None,
    ) -> RunnableConfig:
        """Run config for ``thread_id``, traced in Langfuse when the settings enable it."""
        return run_config(
            thread_id,
            user_id=user_id,
            tags=tags,
            tenant_id=tenant_id,
            connection_id=connection_id,
            settings=self.settings,
        )


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
    table_docs: Mapping[str, TableDocLike] | None = None,
) -> OrchestratorHandle:
    """Build the orchestrator; pass ``model`` to avoid creating a real chat model.

    An ``executor`` that can also introspect the catalog (``list_tables``,
    ``describe_table``, ``distinct_values``) additionally gets the schema tools,
    annotated with ``table_docs`` when given.
    """
    resolved = settings if settings is not None else get_settings()
    if resolved.environment is Environment.PROD and (checkpointer is None or store is None):
        raise ConfigurationError(
            "A durable checkpointer and store are required in prod; "
            "open postgres_checkpointer_cm in the app lifespan and pass both."
        )
    agent_model = model if model is not None else chat_model("orchestrator", resolved)
    saver = checkpointer if checkpointer is not None else build_checkpointer(resolved)

    tools: list[BaseTool] = []
    interrupt_on: dict[str, bool | InterruptOnConfig] = {}
    if executor is not None:
        threshold = resolved.scan_row_threshold
        registry = ApprovalRegistry()
        tools.extend(make_sql_tools(executor, threshold, registry))
        interrupt_on["run_sql"] = _approval_config(executor, threshold, registry)
        if isinstance(executor, SchemaExecutor):
            tools.extend(make_schema_tools(executor, table_docs))

    graph = create_deep_agent(
        model=agent_model,
        tools=[*tools, *extra_tools],
        system_prompt=load_orchestrator_prompt(resolved),
        middleware=[TodoListMiddleware()],
        backend=build_backend(store),
        interrupt_on=interrupt_on,
        checkpointer=saver,
        store=store,
    )
    return OrchestratorHandle(graph=graph, store=store, checkpointer=saver, settings=resolved)
