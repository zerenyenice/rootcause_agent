"""The root-cause subagent always ends in a WhyChain the orchestrator can trust (#138).

``FinalAnswerMiddleware`` (on the subagent): when a run ends without a structured
WhyChain (a budget ended it, or the model stopped with prose or nothing), it makes
one final model call bound only to the WhyChain tool and returns that chain as
partial. If the call fails or yields no valid chain, it returns a synthetic partial
chain whose notes say the analysis did not complete.

``TaskResultGuardMiddleware`` (on the orchestrator): a root-cause ``task`` result
that is still not a WhyChain is replaced by an explicit failure text, so the
orchestrator never receives an empty or free-form answer it could embellish.
"""

import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import ValidationError

from rootcause.agents.reports import ProblemStatement, WhyChain
from rootcause.logging import get_logger

WHY_CHAIN_TOOL = WhyChain.__name__
TASK_TOOL = "task"
FINAL_ANSWER_INSTRUCTION = (
    "The investigation has ended and no more queries can run. Call the WhyChain tool "
    "now with a partial chain (partial=true): keep only the why levels backed by "
    "queries you executed above, set root_cause only if the evidence supports it, and "
    "say in notes what is still open and what to ask the user."
)
INCOMPLETE_NOTES = (
    "The root-cause analysis did not complete: the investigation ended without a "
    "WhyChain, so no finding is backed by evidence. Ask the user to narrow the "
    "problem (what, when, where, impact) or to retry."
)
DEFAULT_PARTIAL_NOTES = "The investigation budget ended the run before a full chain."
FAILED_TASK_RESULT = (
    "The root-cause analysis did not complete: the subagent returned no WhyChain. "
    "Tell the user the analysis did not complete and report no findings."
)
UNKNOWN = "unknown"
# Longest task description kept as the placeholder problem statement.
MAX_PROBLEM_CHARS = 500

_logger = get_logger(__name__)


class FinalAnswerMiddleware(AgentMiddleware[Any, Any, Any]):
    """Guarantee a ``structured_response`` WhyChain when the subagent run ends."""

    def __init__(self, model: BaseChatModel) -> None:
        super().__init__()
        self._model = model

    def after_agent(self, state: Any, runtime: Runtime[Any]) -> dict[str, Any] | None:
        if state.get("structured_response") is not None:
            return None
        messages = list(state.get("messages", ()))
        try:
            reply = self._forced().invoke(_final_request(messages))
        except Exception as exc:  # noqa: BLE001 - any provider failure falls back below
            return _respond(None, messages, error=exc)
        return _respond(reply, messages)

    async def aafter_agent(self, state: Any, runtime: Runtime[Any]) -> dict[str, Any] | None:
        if state.get("structured_response") is not None:
            return None
        messages = list(state.get("messages", ()))
        try:
            reply = await self._forced().ainvoke(_final_request(messages))
        except Exception as exc:  # noqa: BLE001 - any provider failure falls back below
            return _respond(None, messages, error=exc)
        return _respond(reply, messages)

    def _forced(self) -> Any:
        return self._model.bind_tools([WhyChain], tool_choice=WHY_CHAIN_TOOL)


def _final_request(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    return [*messages, HumanMessage(FINAL_ANSWER_INSTRUCTION)]


def _respond(
    reply: Any, messages: Sequence[BaseMessage], *, error: Exception | None = None
) -> dict[str, Any]:
    chain = _chain_from(reply)
    if chain is None:
        # Shape only: never the conversation or the provider's message.
        _logger.warning(
            "root_cause_final_answer_missing",
            error_type=type(error).__name__ if error is not None else None,
        )
        chain = incomplete_chain(messages)
    return {"structured_response": chain}


def _chain_from(reply: Any) -> WhyChain | None:
    """The partial WhyChain of a forced final call; ``None`` when it has none."""
    if not isinstance(reply, AIMessage):
        return None
    for call in reply.tool_calls:
        if call["name"] != WHY_CHAIN_TOOL:
            continue
        try:
            chain = WhyChain.model_validate(call["args"])
        except ValidationError:
            continue
        notes = chain.notes or DEFAULT_PARTIAL_NOTES
        return chain.model_copy(update={"partial": True, "notes": notes})
    return None


def incomplete_chain(messages: Sequence[BaseMessage]) -> WhyChain:
    """A partial chain with no levels, saying the analysis did not complete."""
    return WhyChain(
        problem=ProblemStatement(
            what=_task_description(messages), when=UNKNOWN, where=UNKNOWN, impact=UNKNOWN
        ),
        levels=(),
        root_cause=None,
        partial=True,
        notes=INCOMPLETE_NOTES,
    )


def _task_description(messages: Sequence[BaseMessage]) -> str:
    first = next((m for m in messages if isinstance(m, HumanMessage)), None)
    text = first.text.strip() if first is not None else ""
    return text[:MAX_PROBLEM_CHARS] or UNKNOWN


class TaskResultGuardMiddleware(AgentMiddleware[Any, Any, Any]):
    """Turn a non-WhyChain result of the ``subagent`` task into an explicit failure."""

    def __init__(self, subagent: str) -> None:
        super().__init__()
        self._subagent = subagent

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        return _guard(result) if self._applies(request) else result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        return _guard(result) if self._applies(request) else result

    def _applies(self, request: ToolCallRequest) -> bool:
        call = request.tool_call
        args = call.get("args") or {}
        return call["name"] == TASK_TOOL and args.get("subagent_type") == self._subagent


def _guard(result: ToolMessage | Command[Any]) -> ToolMessage | Command[Any]:
    if isinstance(result, ToolMessage):
        return _checked(result)
    update = result.update
    if not isinstance(update, dict) or "messages" not in update:
        return result
    messages = [
        _checked(m) if isinstance(m, ToolMessage) else m for m in update.get("messages", ())
    ]
    return dataclasses.replace(result, update={**update, "messages": messages})


def _checked(message: ToolMessage) -> ToolMessage:
    chain = parse_why_chain(message.content)
    if chain is not None:
        # Canonical WhyChain JSON: the UI renders the card from exactly this content.
        return message.model_copy(update={"content": chain.model_dump_json()})
    _logger.warning("root_cause_task_result_not_a_why_chain", empty=not message.text.strip())
    return message.model_copy(update={"content": FAILED_TASK_RESULT, "status": "error"})


def parse_why_chain(content: Any) -> WhyChain | None:
    """The WhyChain in JSON ``content``, or ``None`` when it is not one."""
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        return WhyChain.model_validate_json(content)
    except ValidationError:
        return None


def is_why_chain(content: Any) -> bool:
    """True when ``content`` is WhyChain JSON."""
    return parse_why_chain(content) is not None
