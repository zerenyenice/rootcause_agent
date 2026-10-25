"""Declarative DeepAgents subagents delegated to by the orchestrator's ``task`` tool."""

from collections.abc import Sequence
from typing import Any

from deepagents import SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from rootcause.agents.reports import WhyChain
from rootcause.agents.skills import load_skill_body

ROOT_CAUSE_NAME = "root-cause"
ROOT_CAUSE_SKILL = "root-cause"
ROOT_CAUSE_DESCRIPTION = (
    "Runs a query-backed 5 Whys root-cause analysis for one framed problem "
    "(what, when, where, impact) and returns a WhyChain JSON object. Delegate every "
    "'why did this happen?' question here; if the chain comes back partial, its "
    "notes say what to ask the user."
)
# SQL and schema tools only: the subagent answers each why with an executed query.
ROOT_CAUSE_TOOLS = frozenset(
    {"run_sql", "explain_sql", "list_tables", "describe_table", "column_values"}
)
# Graph steps per agent turn: the model node plus the tool node it feeds.
_STEPS_PER_MODEL_CALL = 2


def root_cause_subagent(
    tools: Sequence[BaseTool],
    model: BaseChatModel | str | None = None,
    *,
    max_tool_calls: int | None = None,
    max_steps: int | None = None,
) -> SubAgent:
    """Build the ``root-cause`` SubAgent spec from the tools the orchestrator holds.

    Only the SQL and schema tools are passed on; ``model`` is set only when given,
    so the subagent otherwise inherits the orchestrator's model.

    ``max_tool_calls`` caps the tool calls of one subagent run: further calls are
    answered with a limit error so the model can still return a (partial) WhyChain.
    ``max_steps`` caps the run's graph steps, as a model-call limit of
    ``max_steps // 2`` that ends the run gracefully. A declarative spec cannot
    carry a recursion limit, and ``create_agent`` binds its own (9999).
    """
    spec = SubAgent(
        name=ROOT_CAUSE_NAME,
        description=ROOT_CAUSE_DESCRIPTION,
        system_prompt=load_skill_body(ROOT_CAUSE_SKILL),
        tools=[tool for tool in tools if tool.name in ROOT_CAUSE_TOOLS],
        response_format=WhyChain,
    )
    middleware = _budget_middleware(max_tool_calls, max_steps)
    if middleware:
        spec = SubAgent(**spec, middleware=middleware)
    return spec if model is None else SubAgent(**spec, model=model)


def _budget_middleware(
    max_tool_calls: int | None, max_steps: int | None
) -> list[AgentMiddleware[Any, Any, Any]]:
    middleware: list[AgentMiddleware[Any, Any, Any]] = []
    if max_tool_calls is not None:
        middleware.append(
            ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="continue")
        )
    if max_steps is not None:
        model_calls = max(1, max_steps // _STEPS_PER_MODEL_CALL)
        middleware.append(ModelCallLimitMiddleware(run_limit=model_calls, exit_behavior="end"))
    return middleware
