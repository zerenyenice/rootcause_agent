"""A context-aware fake chat model for orchestrator + root-cause subagent runs (no network).

One instance serves both agents, as in production where the subagent inherits the
orchestrator's model. Each call is routed by the tools bound to it:

- ``task`` bound -> the orchestrator: delegate once, then answer with plain text.
- only ``WhyChain`` bound, or ``tool_choice`` naming ``WhyChain`` -> a forced
  structured call: answer with a partial WhyChain tool call when ``cooperate``,
  otherwise with ``refusal`` (the model ignores the constraint).
- anything else -> the subagent mid-run: ``subagent_reply`` decides (run_sql
  calls, plain text or an empty message); it never volunteers a WhyChain.

Every call records the messages the model received and the tool names bound,
so tests can inspect exactly what a provider would have been sent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool

TASK_ARGS = {
    "description": (
        "What: scrap_rate up 200%. When: 2025-12-11..2025-12-15. Where: line L01. Impact: 10k EUR."
    ),
    "subagent_type": "root-cause",
}
PARTIAL_CHAIN: dict[str, Any] = {
    "problem": {"what": "scrap up", "when": "May", "where": "L01", "impact": "10k EUR"},
    "levels": [],
    "root_cause": None,
    "partial": True,
    "notes": "The investigation budget ended the run before a full chain.",
}
SubagentReply = Literal["sql", "parallel_sql", "text", "empty"]


@dataclass
class Call:
    """One model invocation: the bound tool names and the messages it received."""

    tools: tuple[str, ...]
    tool_choice: Any
    messages: tuple[BaseMessage, ...]

    @property
    def role(self) -> str:
        if "task" in self.tools:
            return "orchestrator"
        if _forced(self.tools, self.tool_choice):
            return "forced"
        return "subagent"


@dataclass
class Recorder:
    calls: list[Call] = field(default_factory=list)
    counter: int = 0

    def next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict) and "name" in tool:
        return str(tool["name"])
    return str(convert_to_openai_tool(tool)["function"]["name"])


def _choice_names_why_chain(tool_choice: Any) -> bool:
    if tool_choice == "WhyChain":
        return True
    if isinstance(tool_choice, dict):
        name = tool_choice.get("name") or tool_choice.get("function", {}).get("name")
        return bool(name == "WhyChain")
    return False


def _forced(tools: Sequence[str], tool_choice: Any) -> bool:
    only_chain = bool(tools) and set(tools) <= {"WhyChain"}
    return only_chain or _choice_names_why_chain(tool_choice)


class RoutingChatModel(BaseChatModel):
    """See the module docstring."""

    recorder: Recorder
    subagent_reply: SubagentReply = "sql"
    parallel: int = 3
    cooperate: bool = True
    refusal: str = ""
    bound: tuple[str, ...] = ()
    choice: Any = None

    @property
    def _llm_type(self) -> str:
        return "routing-fake"

    def bind_tools(  # type: ignore[override]
        self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any
    ) -> Runnable[Any, BaseMessage]:
        names = tuple(_tool_name(tool) for tool in tools)
        return self.model_copy(update={"bound": names, "choice": tool_choice})

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        call = Call(self.bound, self.choice, tuple(messages))
        self.recorder.calls.append(call)
        return ChatResult(generations=[ChatGeneration(message=self._reply(call))])

    def _reply(self, call: Call) -> AIMessage:
        if call.role == "orchestrator":
            delegated = any(isinstance(m, ToolMessage) for m in call.messages)
            if delegated:
                return AIMessage(content="final answer relayed to the user")
            return self._calls(("task", TASK_ARGS))
        if call.role == "forced":
            if self.cooperate:
                return self._calls(("WhyChain", PARTIAL_CHAIN))
            return AIMessage(content=self.refusal)
        return self._subagent_turn()

    def _subagent_turn(self) -> AIMessage:
        if self.subagent_reply == "text":
            return AIMessage(content=self.refusal or "I looked around but have no chain.")
        if self.subagent_reply == "empty":
            return AIMessage(content="")
        count = self.parallel if self.subagent_reply == "parallel_sql" else 1
        return self._calls(
            *[("run_sql", {"sql": "select 1", "purpose": "probe"}) for _ in range(count)]
        )

    def _calls(self, *calls: tuple[str, dict[str, Any]]) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {"name": name, "args": args, "id": self.recorder.next_id(name)}
                for name, args in calls
            ],
        )


def routing_model(**kwargs: Any) -> RoutingChatModel:
    return RoutingChatModel(recorder=Recorder(), **kwargs)
