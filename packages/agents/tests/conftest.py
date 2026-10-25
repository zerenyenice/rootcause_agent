"""Shared offline fixtures: a scripted chat model and a recording SQL executor."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable


class ScriptedChatModel(FakeMessagesListChatModel):
    """Replays a fixed list of AIMessages and tolerates ``bind_tools``."""

    def bind_tools(  # type: ignore[override]
        self, tools: Sequence[Any], **kwargs: Any
    ) -> Runnable[Any, BaseMessage]:
        return self


def _tool_call_message(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _scripted_model(*messages: AIMessage) -> ScriptedChatModel:
    return ScriptedChatModel(responses=list(messages))


@dataclass
class FakeExecutor:
    """Records calls; never touches a database."""

    estimated_rows: int = 10
    estimated_cost: float = 1.5
    explain_calls: list[str] = field(default_factory=list)
    run_calls: list[tuple[str, str]] = field(default_factory=list)
    error: Exception | None = None

    def explain(self, sql: str) -> Any:
        from rootcause_agents.tools.sql import QueryEstimate

        self.explain_calls.append(sql)
        if self.error is not None:
            raise self.error
        return QueryEstimate(estimated_rows=self.estimated_rows, estimated_cost=self.estimated_cost)

    def run(self, sql: str, purpose: str) -> Any:
        from rootcause_agents.tools.sql import QueryResult

        self.run_calls.append((sql, purpose))
        if self.error is not None:
            raise self.error
        return QueryResult(columns=("n",), rows=((1,),), row_count=1, truncated=False, sql=sql)


@pytest.fixture
def executor() -> FakeExecutor:
    return FakeExecutor()


@pytest.fixture
def make_executor() -> Callable[..., FakeExecutor]:
    return FakeExecutor


@pytest.fixture
def scripted_model() -> Callable[..., ScriptedChatModel]:
    return _scripted_model


@pytest.fixture
def tool_call_message() -> Callable[[str, dict[str, Any], str], AIMessage]:
    return _tool_call_message
