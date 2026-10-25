"""Orchestrator construction (no graph is created at import time)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from deepagents import SubAgent, create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_config
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from rootcause.agents.backends import build_backend, build_checkpointer
from rootcause.agents.prompts import load_orchestrator_prompt
from rootcause.agents.subagents import root_cause_subagent
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
    # ``None`` when compiled without one (LangGraph Server injects its own).
    checkpointer: BaseCheckpointSaver[Any] | None
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


# Opt-out for ``build_orchestrator(checkpointer=...)``: compile without a checkpointer.
NoCheckpointer = Literal[False]


def _current_thread_id() -> str:
    """Thread id of the running graph (``""`` outside a runnable context)."""
    try:
        return thread_id_of(get_config())
    except RuntimeError:
        return ""


def _describe(sql: str, decision: ApprovalDecision, threshold: int) -> str:
    """Approval text naming the SQL and its estimated rows; never an error message."""
    if decision.estimate is None:
        estimate = "Estimated rows: unavailable (the planner estimate failed)."
    else:
        estimate = (
            f"Estimated rows: {decision.estimate.estimated_rows:,} "
            f"(approval threshold {threshold:,})."
        )
    return f"run_sql needs approval.\n{estimate}\nSQL:\n{sql}"


def _approval_config(
    executor: QueryExecutor, threshold: int, registry: ApprovalRegistry
) -> InterruptOnConfig:
    def evaluate(sql: str) -> ApprovalDecision:
        try:
            estimate = executor.explain(sql)
        except Exception:  # noqa: BLE001 - fail closed: a human decides when unsure
            return ApprovalDecision(requires_approval=True, estimate=None)
        return ApprovalDecision(estimate.estimated_rows > threshold, estimate)

    def decide(thread_id: str, tool_call: Any) -> ApprovalDecision:
        sql = str(tool_call["args"].get("sql", ""))
        return registry.decide((thread_id, str(tool_call["id"])), lambda: evaluate(sql))

    def needs_approval(request: Any) -> bool:
        thread_id = thread_id_of(request.runtime.config)
        return decide(thread_id, request.tool_call).requires_approval

    def description(tool_call: Any, state: Any, runtime: Any) -> str:
        # Runs right after ``needs_approval``, so the cached decision is reused.
        decision = decide(_current_thread_id(), tool_call)
        return _describe(str(tool_call["args"].get("sql", "")), decision, threshold)

    return InterruptOnConfig(
        allowed_decisions=["approve", "reject"], when=needs_approval, description=description
    )


def _sql_wiring(
    executor: QueryExecutor | None,
    threshold: int,
    table_docs: Mapping[str, TableDocLike] | None,
) -> tuple[list[BaseTool], dict[str, bool | InterruptOnConfig], list[SubAgent]]:
    """Warehouse tools, the run_sql approval gate and the root-cause subagent."""
    if executor is None:
        return [], {}, []
    registry = ApprovalRegistry()
    tools = list(make_sql_tools(executor, threshold, registry))
    if isinstance(executor, SchemaExecutor):
        tools = [*tools, *make_schema_tools(executor, table_docs)]
    interrupt_on: dict[str, bool | InterruptOnConfig] = {
        "run_sql": _approval_config(executor, threshold, registry)
    }
    return tools, interrupt_on, [root_cause_subagent(tools)]


def build_orchestrator(
    *,
    model: BaseChatModel | None = None,
    settings: Settings | None = None,
    store: BaseStore | None = None,
    checkpointer: BaseCheckpointSaver[Any] | NoCheckpointer | None = None,
    executor: QueryExecutor | None = None,
    extra_tools: Sequence[BaseTool] = (),
    table_docs: Mapping[str, TableDocLike] | None = None,
) -> OrchestratorHandle:
    """Build the orchestrator; pass ``model`` to avoid creating a real chat model.

    An ``executor`` adds the SQL tools (gated by a run_sql approval interrupt) and
    the ``root-cause`` subagent; one that can also introspect the catalog
    (``list_tables``, ``describe_table``, ``distinct_values``) additionally gets
    the schema tools, annotated with ``table_docs`` when given.

    ``checkpointer=None`` builds the default saver; ``checkpointer=False`` compiles
    without one, for hosts that inject their own (LangGraph Server, which also
    provides the store, so the prod durability check does not apply).
    """
    resolved = settings if settings is not None else get_settings()
    server_managed = checkpointer is False
    if (
        resolved.environment is Environment.PROD
        and not server_managed
        and (checkpointer is None or store is None)
    ):
        raise ConfigurationError(
            "A durable checkpointer and store are required in prod; "
            "open postgres_checkpointer_cm in the app lifespan and pass both."
        )
    agent_model = model if model is not None else chat_model("orchestrator", resolved)
    saver = _resolve_checkpointer(checkpointer, resolved)
    tools, interrupt_on, subagents = _sql_wiring(executor, resolved.scan_row_threshold, table_docs)

    graph = create_deep_agent(
        model=agent_model,
        tools=[*tools, *extra_tools],
        system_prompt=load_orchestrator_prompt(resolved),
        middleware=[TodoListMiddleware()],
        subagents=subagents,
        backend=build_backend(store),
        interrupt_on=interrupt_on,
        checkpointer=saver,
        store=store,
    )
    return OrchestratorHandle(graph=graph, store=store, checkpointer=saver, settings=resolved)


def _resolve_checkpointer(
    checkpointer: BaseCheckpointSaver[Any] | NoCheckpointer | None, settings: Settings
) -> BaseCheckpointSaver[Any] | None:
    if checkpointer is False:
        return None
    return checkpointer if checkpointer is not None else build_checkpointer(settings)
