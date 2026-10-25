"""Placeholder LangGraph registered in ``langgraph.json`` (replaced in T030)."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class EchoState(TypedDict):
    messages: list[str]


def echo(state: EchoState) -> EchoState:
    """Append ``echo: <last message>``; an empty history is returned unchanged."""
    messages = state["messages"]
    if not messages:
        return {"messages": list(messages)}
    return {"messages": [*messages, f"echo: {messages[-1]}"]}


def build_graph() -> StateGraph[EchoState, None, EchoState, EchoState]:
    builder = StateGraph(EchoState)
    builder.add_node("echo", echo)
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    return builder


graph = build_graph().compile()
