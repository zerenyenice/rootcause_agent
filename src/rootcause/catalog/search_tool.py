"""``search_catalog``: semantic search over a connection's indexed, still-selected catalog.

The tool embeds the query, asks the repository for the most similar entries
(cosine) and returns them with their current descriptions. ``k`` is clamped to
1..20. Failures return a generic ``{"error": ...}`` payload and are logged by
exception type only (provider and driver messages can carry endpoints or names).
"""

import math
import uuid
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.tools import BaseTool, StructuredTool

from rootcause.catalog.records import SearchHit
from rootcause.catalog.repository import CatalogRepository
from rootcause.logging import get_logger
from rootcause.settings import Settings

SEARCH_TOOL = "search_catalog"
DEFAULT_K = 5
MIN_K = 1
MAX_K = 20
BLANK_QUERY_MESSAGE = "The query is empty; describe the data you are looking for."
FAILURE_MESSAGE = "The catalog search could not be completed."
DESCRIPTION = (
    "Find the warehouse tables and columns most relevant to a question, by meaning. "
    "Returns up to k (1-20, default 5) entries with kind ('table' or 'column'), schema, "
    "table, column, description and a similarity score; use it before describe_table "
    "when you do not know which tables hold the data."
)

_logger = get_logger(__name__)


def _hit(hit: SearchHit) -> dict[str, Any]:
    return {
        "kind": hit.kind,
        "schema": hit.schema,
        "table": hit.table,
        "column": hit.column,
        "description": hit.description,
        "score": max(-1.0, min(1.0, hit.score)),
    }


def make_search_catalog_tool(
    connection_id: uuid.UUID,
    *,
    catalog: CatalogRepository,
    embedder: Embeddings,
    settings: Settings,
) -> BaseTool:
    """``search_catalog`` bound to one connection's catalog."""

    async def search_catalog(query: str, k: int = DEFAULT_K) -> dict[str, Any]:
        if not query.strip():
            return {"error": BLANK_QUERY_MESSAGE}
        limit = min(max(k, MIN_K), MAX_K)
        try:
            vector = await embedder.aembed_query(query)
            if len(vector) != settings.embedding_dimensions or not all(
                math.isfinite(value) for value in vector
            ):
                raise ValueError("the query embedding does not match the configured dimension")
            hits = await catalog.search(connection_id, vector, limit)
        except Exception as error:  # noqa: BLE001 - surfaced to the model as a payload
            _logger.warning(
                "search_catalog_failed",
                connection_id=str(connection_id),
                error_type=type(error).__name__,
            )
            return {"error": FAILURE_MESSAGE}
        return {"results": [_hit(hit) for hit in hits]}

    return StructuredTool.from_function(
        coroutine=search_catalog, name=SEARCH_TOOL, description=DESCRIPTION
    )
