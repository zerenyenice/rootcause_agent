"""Declarative DeepAgents subagents delegated to by the orchestrator's ``task`` tool."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from deepagents import SubAgent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from rootcause.agents.final_answer import FinalAnswerMiddleware
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
# SQL, schema and catalog search tools only: the subagent answers each why with an
# executed query.
ROOT_CAUSE_TOOLS = frozenset(
    {"run_sql", "explain_sql", "list_tables", "describe_table", "column_values", "search_catalog"}
)
# The only subagent tool gated behind human approval.
APPROVAL_TOOL = "run_sql"
# Graph steps per agent turn: the model node plus the tool node it feeds.
_STEPS_PER_MODEL_CALL = 2


def root_cause_subagent(
    tools: Sequence[BaseTool],
    model: BaseChatModel | str | None = None,
    *,
    max_tool_calls: int | None = None,
    max_steps: int | None = None,
    approval: InterruptOnConfig | None = None,
    answer_model: BaseChatModel | None = None,
) -> SubAgent:
    """Build the ``root-cause`` SubAgent spec from the tools the orchestrator holds.

    Only the SQL, schema and catalog search tools are passed on; ``model`` is set only when given,
    so the subagent otherwise inherits the orchestrator's model.

    ``max_tool_calls`` caps the tool calls of one subagent run: further calls are
    answered with a limit error so the model can still return a (partial) WhyChain.
    ``max_steps`` caps the run's graph steps, as a model-call limit of
    ``max_steps // 2`` that ends the run gracefully. A declarative spec cannot
    carry a recursion limit, and ``create_agent`` binds its own (9999). Both
    budgets are thread limits, so approval interrupts and resumes do not reset them.

    ``approval`` gates ``run_sql`` behind a human decision. The gate is placed
    before the budgets in the middleware list, so their ``after_model`` hooks run
    first and a call the tool budget already blocked is never sent for approval
    (one tool message per call, #132/#139).

    ``answer_model`` (default: ``model`` when it is a chat model) makes the run
    always end in a WhyChain: if it ends without one, a final call bound to the
    WhyChain tool returns a partial chain (#138).
    """
    spec = SubAgent(
        name=ROOT_CAUSE_NAME,
        description=ROOT_CAUSE_DESCRIPTION,
        system_prompt=load_skill_body(ROOT_CAUSE_SKILL),
        tools=[tool for tool in tools if tool.name in ROOT_CAUSE_TOOLS],
        response_format=ToolStrategy(WhyChain),
    )
    final_model = answer_model if answer_model is not None else _chat_model(model)
    middleware = [
        *_approval_middleware(approval),
        *_budget_middleware(max_tool_calls, max_steps),
        *([FinalAnswerMiddleware(final_model)] if final_model is not None else []),
    ]
    if approval is not None:
        # An empty mapping stops DeepAgents from appending its own approval gate
        # after the budgets (and from inheriting the orchestrator's interrupt_on).
        spec = SubAgent(**spec, interrupt_on={})
    if middleware:
        spec = SubAgent(**spec, middleware=middleware)
    return spec if model is None else SubAgent(**spec, model=model)


def _chat_model(model: BaseChatModel | str | None) -> BaseChatModel | None:
    return model if isinstance(model, BaseChatModel) else None


def _approval_middleware(
    approval: InterruptOnConfig | None,
) -> list[AgentMiddleware[Any, Any, Any]]:
    if approval is None:
        return []
    gate = InterruptOnConfig(**{**approval, "when": _unanswered_and(approval.get("when"))})
    return [HumanInTheLoopMiddleware(interrupt_on={APPROVAL_TOOL: gate})]


def _unanswered_and(
    when: Callable[[ToolCallRequest], bool] | None,
) -> Callable[[ToolCallRequest], bool]:
    """Approval predicate that skips calls already answered (e.g. by the tool budget)."""

    def needs_approval(request: ToolCallRequest) -> bool:
        if _is_answered(request):
            return False
        return when(request) if when is not None else True

    return needs_approval


def _is_answered(request: ToolCallRequest) -> bool:
    state = request.state if isinstance(request.state, Mapping) else {}
    call_id = request.tool_call["id"]
    return any(
        isinstance(message, ToolMessage) and message.tool_call_id == call_id
        for message in state.get("messages", ())
    )


def _budget_middleware(
    max_tool_calls: int | None, max_steps: int | None
) -> list[AgentMiddleware[Any, Any, Any]]:
    middleware: list[AgentMiddleware[Any, Any, Any]] = []
    if max_tool_calls is not None:
        middleware.append(
            ToolCallLimitMiddleware(thread_limit=max_tool_calls, exit_behavior="continue")
        )
    if max_steps is not None:
        model_calls = max(1, max_steps // _STEPS_PER_MODEL_CALL)
        middleware.append(ModelCallLimitMiddleware(thread_limit=model_calls, exit_behavior="end"))
    return middleware
