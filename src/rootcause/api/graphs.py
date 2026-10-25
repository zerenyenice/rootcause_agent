"""Graph factories: ``langgraph.json`` (``rootcause``) and the agent service.

Importing this module builds nothing. LangGraph Server calls :func:`make_graph`
and injects its own checkpointer and store; the agent service
(:mod:`rootcause.agent_service`) calls :func:`make_service_graph` once at
startup with the checkpointer and store it owns.
"""

import atexit
import threading
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from rootcause.agents.orchestrator import build_orchestrator
from rootcause.agents.tools.sql import QueryExecutor
from rootcause.connections.duckdb_executor import executor_from_settings
from rootcause.evals.manufacturing.table_docs import TABLE_DOCS
from rootcause.logging import get_logger
from rootcause.settings import Settings, get_settings

_logger = get_logger(__name__)
# One warehouse executor per configured path for the process lifetime; ``None`` is
# cached too (no warehouse configured). Closed at interpreter exit.
_executors: dict[Path | None, QueryExecutor | None] = {}
_executors_lock = threading.Lock()
_close_registered = False


def make_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the orchestrator from settings, without a checkpointer.

    With ``ROOTCAUSE_WAREHOUSE_PATH`` set it gets the SQL and schema tools and the
    root-cause subagent; without it the orchestrator can only frame the problem.
    The warehouse is opened once per path and reused by every graph built here;
    it stays open for the server process's lifetime and closes at exit. Packaged
    skill and prompt files are cached per process, so once warmed this does no
    file I/O (the server calls it inside its event loop).

    Raises:
        ConfigurationError: the settings are invalid or the warehouse file is missing.
    """
    settings = get_settings()
    return build_orchestrator(
        settings=settings,
        checkpointer=False,
        executor=_shared_executor(settings),
        table_docs=TABLE_DOCS,
    ).graph


def make_service_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any],
    store: BaseStore | None = None,
    settings: Settings | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the orchestrator compiled with the caller's ``checkpointer`` and ``store``.

    Same tools and warehouse executor as :func:`make_graph`. Blocking (it may open
    the warehouse file): call it once, from a worker thread in async code.

    Raises:
        ConfigurationError: the settings are invalid, the warehouse file is missing,
            or ``store`` is missing in prod.
    """
    resolved = settings if settings is not None else get_settings()
    return build_orchestrator(
        settings=resolved,
        checkpointer=checkpointer,
        store=store,
        executor=_shared_executor(resolved),
        table_docs=TABLE_DOCS,
    ).graph


def _shared_executor(settings: Settings) -> QueryExecutor | None:
    global _close_registered
    key = settings.warehouse_path
    with _executors_lock:
        if key not in _executors:
            _executors[key] = executor_from_settings(settings)
            if not _close_registered:
                atexit.register(_close_executors)
                _close_registered = True
        return _executors[key]


def _close_executors() -> None:
    with _executors_lock:
        executors = list(_executors.values())
        _executors.clear()
    for executor in executors:
        # ``QueryExecutor`` has no ``close``; one without it owns its lifetime.
        close = getattr(executor, "close", None)
        if close is None:
            continue
        try:
            close()
        except Exception:  # noqa: BLE001 - interpreter exit: report and close the rest
            _logger.warning("warehouse_close_failed", exc_info=True)
