"""Declarative DeepAgents subagents delegated to by the orchestrator's ``task`` tool."""

from collections.abc import Sequence

from deepagents import SubAgent
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


def root_cause_subagent(
    tools: Sequence[BaseTool], model: BaseChatModel | str | None = None
) -> SubAgent:
    """Build the ``root-cause`` SubAgent spec from the tools the orchestrator holds.

    Only the SQL and schema tools are passed on; ``model`` is set only when given,
    so the subagent otherwise inherits the orchestrator's model.
    """
    spec = SubAgent(
        name=ROOT_CAUSE_NAME,
        description=ROOT_CAUSE_DESCRIPTION,
        system_prompt=load_skill_body(ROOT_CAUSE_SKILL),
        tools=[tool for tool in tools if tool.name in ROOT_CAUSE_TOOLS],
        response_format=WhyChain,
    )
    return spec if model is None else SubAgent(**spec, model=model)
