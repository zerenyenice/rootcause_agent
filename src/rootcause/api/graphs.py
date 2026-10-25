"""Graph factory registered in ``langgraph.json`` as ``rootcause``.

Importing this module builds nothing; LangGraph Server calls :func:`make_graph`
and injects its own checkpointer and store.
"""

from typing import Any

from langgraph.graph.state import CompiledStateGraph

from rootcause.agents.orchestrator import build_orchestrator
from rootcause.connections.duckdb_executor import executor_from_settings
from rootcause.evals.manufacturing.table_docs import TABLE_DOCS
from rootcause.settings import get_settings


def make_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the orchestrator from settings, without a checkpointer.

    With ``ROOTCAUSE_WAREHOUSE_PATH`` set it gets the SQL and schema tools and the
    root-cause subagent; without it the orchestrator can only frame the problem.
    The warehouse connection stays open for the server process's lifetime.

    Raises:
        ConfigurationError: the settings are invalid or the warehouse file is missing.
    """
    settings = get_settings()
    return build_orchestrator(
        settings=settings,
        checkpointer=False,
        executor=executor_from_settings(settings),
        table_docs=TABLE_DOCS,
    ).graph
